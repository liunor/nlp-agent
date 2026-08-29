"""Bounded retry, timeout, circuit breaking, failover, and streaming semantics."""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from langchain_core.messages import AIMessage, AIMessageChunk, message_chunk_to_message

from core.model_runtime.contracts import (
    CircuitBreakerPolicy,
    ModelDefinition,
    ModelPresetConfig,
)
from core.model_runtime.normalization import (
    error_canonical_usage,
    extract_provider_response_id,
    normalize_chunk,
    normalize_message,
    response_canonical_usage,
    response_usage,
)
from core.model_runtime.usage import (
    CanonicalTokenUsage,
    InvocationOutcome,
    InvocationStatus,
    ModelIdentity,
    ModelInvocation,
    resolve_usage_attribution,
)
from core.observability.context import current_telemetry_context
from core.observability.models import SpanKind, SpanStatus
from core.observability.runtime import global_telemetry
from utils.logger import get_logger


logger = get_logger("nlp_agent.model_runtime")


class ModelRuntimeExhaustedError(RuntimeError):
    pass


class EmptyModelResponseError(RuntimeError):
    pass


class StreamInterruptedError(RuntimeError):
    """A stream failed after externally visible output; transparent replay is unsafe."""

    def __init__(self, message: str, *, provider: str, model: str) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model


class _ReporterFailure(BaseException):
    """Keep Reporter failures out of Provider retry/error classification."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.cause = cause


@dataclass
class CircuitState:
    failures: int = 0
    open_until: float = 0.0

    def available(self) -> bool:
        return time.monotonic() >= self.open_until

    def succeed(self) -> None:
        self.failures = 0
        self.open_until = 0.0

    def fail(self, policy: CircuitBreakerPolicy) -> None:
        self.failures += 1
        if self.failures >= policy.failure_threshold:
            self.open_until = time.monotonic() + policy.cooldown_s


@dataclass
class ModelCandidate:
    preset_name: str
    provider_name: str
    model_name: str
    definition: ModelDefinition
    preset: ModelPresetConfig
    model: Any
    circuit: CircuitState = field(default_factory=CircuitState)


@dataclass(frozen=True)
class ErrorDecision:
    retryable: bool
    kind: str
    retry_after_s: float | None = None


def classify_model_error(error: BaseException) -> ErrorDecision:
    if isinstance(error, EmptyModelResponseError):
        return ErrorDecision(True, "upstream_empty_response")
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return ErrorDecision(True, "upstream_timeout")
    status = getattr(error, "status_code", None)
    message = str(error).lower()
    code = str(getattr(error, "code", "") or "").lower()
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        details = body.get("error", body)
        code = str(details.get("code", code) or code).lower()
        message = f"{message} {details.get('type', '')} {details.get('message', '')}".lower()
    quota_markers = (
        "insufficient_quota",
        "quota_exceeded",
        "insufficient balance",
        "payment_required",
        "out of credits",
        "billing",
    )
    if any(marker in f"{code} {message}" for marker in quota_markers):
        return ErrorDecision(False, "upstream_provider_quota_exhausted")
    if status == 402:
        return ErrorDecision(False, "upstream_provider_quota_exhausted")

    retry_after = None
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        try:
            retry_after = max(0.0, float(headers.get("retry-after")))
        except (TypeError, ValueError):
            retry_after = None

    if status == 429:
        return ErrorDecision(True, "upstream_rate_limited", retry_after)
    if status in {401, 403}:
        return ErrorDecision(False, "upstream_auth_failed")
    if status == 404:
        return ErrorDecision(False, "upstream_model_unavailable")
    if status in {400, 422}:
        if "context" in message and "length" in message:
            return ErrorDecision(False, "upstream_context_length_exceeded")
        return ErrorDecision(False, "upstream_invalid_request")
    if status in {408, 409} or (isinstance(status, int) and status >= 500):
        return ErrorDecision(True, "upstream_overloaded", retry_after)

    transient = ("timeout", "timed out")
    if any(marker in message for marker in transient):
        return ErrorDecision(True, "upstream_timeout")
    connection = ("connection", "reset")
    if any(marker in message for marker in connection):
        return ErrorDecision(True, "upstream_connection_error")
    overloaded = ("overloaded", "temporarily unavailable")
    if any(marker in message for marker in overloaded):
        return ErrorDecision(True, "upstream_overloaded")

    return ErrorDecision(False, "upstream_unknown")


@asynccontextmanager
async def _attempt_span(
    candidate: ModelCandidate,
    attempt: int,
    fallback_index: int,
    operation_id: str | None = None,
):
    context = current_telemetry_context()
    if context is None:
        yield None
        return
    attributes: dict[str, Any] = {
        "provider": candidate.provider_name,
        "model": candidate.definition.model_id,
        "preset": candidate.preset_name,
        "fallback_index": fallback_index,
        "thinking_enabled": candidate.preset.thinking.enabled,
        "reasoning_effort": candidate.preset.thinking.effort.value,
    }
    if operation_id is not None:
        attributes["operation_id"] = operation_id
    async with global_telemetry.span(
        SpanKind.MODEL,
        "model.request",
        context=context,
        attempt=attempt,
        attributes=attributes,
    ) as span:
        yield span


class ResilientChatModel:
    """LangChain-compatible facade over a capability-compatible candidate chain."""

    emits_model_telemetry = True

    def __init__(
        self,
        candidates: list[ModelCandidate],
        *,
        normalize_response: bool = True,
        model_profile: str | None = None,
        route: str | None = None,
        reporter_slot: Any = None,
        caller_include_raw: bool = False,
    ) -> None:
        if not candidates:
            raise ValueError("At least one model candidate is required")
        self.candidates = candidates
        self.normalize_response = normalize_response
        self.model_profile = model_profile
        self.route = route
        self.reporter_slot = reporter_slot
        self.caller_include_raw = caller_include_raw
        self.model_name = candidates[0].definition.model_id
        self.context_window_tokens = min(
            candidate.definition.context_window_tokens for candidate in candidates
        )
        self.max_output_tokens = max(
            candidate.preset.generation.max_output_tokens for candidate in candidates
        )

    def bind_tools(self, tools: list[Any], **kwargs: Any) -> "ResilientChatModel":
        return ResilientChatModel(
            [
                ModelCandidate(
                    preset_name=item.preset_name,
                    provider_name=item.provider_name,
                    model_name=item.model_name,
                    definition=item.definition,
                    preset=item.preset,
                    model=item.model.bind_tools(tools, **kwargs),
                    circuit=item.circuit,
                )
                for item in self.candidates
            ],
            normalize_response=self.normalize_response,
            model_profile=self.model_profile,
            route=self.route,
            reporter_slot=self.reporter_slot,
            caller_include_raw=self.caller_include_raw,
        )

    def with_structured_output(
        self, schema: Any, **kwargs: Any
    ) -> "ResilientChatModel":
        caller_include_raw = kwargs.get("include_raw", False)
        underlying_kwargs = dict(kwargs)
        underlying_kwargs["include_raw"] = True
        return ResilientChatModel(
            [
                ModelCandidate(
                    preset_name=item.preset_name,
                    provider_name=item.provider_name,
                    model_name=item.model_name,
                    definition=item.definition,
                    preset=item.preset,
                    model=item.model.with_structured_output(
                        schema, **underlying_kwargs
                    ),
                    circuit=item.circuit,
                )
                for item in self.candidates
            ],
            normalize_response=False,
            model_profile=self.model_profile,
            route=self.route,
            reporter_slot=self.reporter_slot,
            caller_include_raw=caller_include_raw,
        )

    async def _report_attempt(
        self,
        *,
        invocation: ModelInvocation | None,
        usage: CanonicalTokenUsage,
        status: InvocationStatus,
        finish_reason: str | None = None,
        error_kind: str | None = None,
    ) -> None:
        if invocation is None:
            return
        if self.reporter_slot is None or self.reporter_slot.reporter is None:
            return
        outcome = InvocationOutcome(
            status=status,
            finish_reason=finish_reason,
            error_kind=error_kind,
            completed_at=datetime.now(timezone.utc),
        )
        await self.reporter_slot.reporter.report(invocation, usage, outcome)

    async def _report_attempt_guarded(self, **kwargs: Any) -> None:
        """Tag Reporter failures while executing inside a Provider try block."""
        try:
            await self._report_attempt(**kwargs)
        except BaseException as error:
            raise _ReporterFailure(error) from error

    def _prepare_invocation(
        self,
        candidate: ModelCandidate,
        attempt: int,
        fallback_index: int,
    ) -> tuple[ModelInvocation | None, str]:
        operation_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)
        has_reporter = (
            self.reporter_slot is not None
            and self.reporter_slot.reporter is not None
        )
        if has_reporter:
            attribution = resolve_usage_attribution()
        else:
            try:
                attribution = resolve_usage_attribution()
            except Exception:
                attribution = None

        if attribution is None:
            return None, operation_id

        identity = ModelIdentity(
            provider=candidate.provider_name,
            provider_model=candidate.definition.model_id,
            model_profile=self.model_profile,
            preset=candidate.preset_name,
            route=self.route,
            pricing_key=candidate.definition.pricing_key,
            context_window_tokens=candidate.definition.context_window_tokens,
            max_output_tokens=candidate.preset.generation.max_output_tokens,
        )
        invocation = ModelInvocation(
            operation_id=operation_id,
            identity=identity,
            attribution=attribution,
            attempt=attempt,
            fallback_index=fallback_index,
            started_at=started_at,
        )
        return invocation, operation_id

    @staticmethod
    def _delay(
        candidate: ModelCandidate, attempt: int, decision: ErrorDecision
    ) -> float:
        if decision.retry_after_s is not None:
            return min(candidate.preset.retry.max_delay_s, decision.retry_after_s)
        cap = min(
            candidate.preset.retry.max_delay_s,
            candidate.preset.retry.base_delay_s * (2 ** max(0, attempt - 1)),
        )
        return (
            random.uniform(0, cap)
            if candidate.preset.retry.jitter == "full"
            else cap
        )

    @staticmethod
    def _visible_chunk(chunk: Any) -> bool:
        if getattr(chunk, "content", None):
            return True
        if getattr(chunk, "tool_call_chunks", None) or getattr(
            chunk, "tool_calls", None
        ):
            return True
        additional = getattr(chunk, "additional_kwargs", None) or {}
        return bool(additional.get("reasoning_content"))

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        if self.normalize_response:
            combined: Any = None
            async for chunk in self.astream(input, config=config, **kwargs):
                combined = chunk if combined is None else combined + chunk
            if combined is None:
                raise ModelRuntimeExhaustedError(
                    "Model stream completed without a response"
                )
            message = (
                message_chunk_to_message(combined)
                if isinstance(combined, AIMessageChunk)
                else combined
            )
            if not isinstance(message, AIMessage):
                raise TypeError(
                    f"Provider returned {type(message).__name__}, expected AIMessage"
                )
            return normalize_message(message)

        last_error: BaseException | None = None
        for fallback_index, candidate in enumerate(self.candidates):
            if not candidate.circuit.available():
                global_telemetry.event(
                    "model.circuit_open",
                    level="warning",
                    payload={
                        "provider": candidate.provider_name,
                        "model": candidate.definition.model_id,
                        "preset": candidate.preset_name,
                    },
                )
                continue
            for attempt in range(1, candidate.preset.retry.max_attempts + 1):
                invocation, operation_id = self._prepare_invocation(
                    candidate, attempt, fallback_index
                )
                attempt_reported = False
                try:
                    async with _attempt_span(
                        candidate, attempt, fallback_index, operation_id
                    ) as span:
                        response = await asyncio.wait_for(
                            candidate.model.ainvoke(input, config=config, **kwargs),
                            timeout=candidate.preset.timeouts.total_s,
                        )

                        is_structured = (
                            isinstance(response, dict) and "raw" in response
                        )
                        if is_structured:
                            raw_msg = response["raw"]
                            parsed = response.get("parsed")
                            parsing_error = response.get("parsing_error")
                            canon_usage = response_canonical_usage(raw_msg)
                            finish_reason = (
                                getattr(raw_msg, "response_metadata", {}) or {}
                            ).get("finish_reason")
                            resp_id = extract_provider_response_id(raw_msg)
                            if (
                                resp_id
                                and canon_usage.provider_response_id is None
                            ):
                                canon_usage = canon_usage.model_copy(
                                    update={"provider_response_id": resp_id}
                                )

                            if span is not None:
                                usage_meta = response_usage(raw_msg)
                                if usage_meta.get("total_tokens"):
                                    span.set_usage(usage_meta)
                                span.annotate(
                                    structured_output=True,
                                    finish_reason=finish_reason or "",
                                )
                                if parsing_error is not None:
                                    span.set_status(
                                        SpanStatus.ERROR,
                                        error_kind=(
                                            "structured_output_parse_error"
                                        ),
                                        error_message=str(parsing_error),
                                    )
                        else:
                            parsing_error = None
                            canon_usage = response_canonical_usage(response)
                            finish_reason = (
                                getattr(response, "response_metadata", {}) or {}
                            ).get("finish_reason")
                            resp_id = extract_provider_response_id(response)
                            if (
                                resp_id
                                and canon_usage.provider_response_id is None
                            ):
                                canon_usage = canon_usage.model_copy(
                                    update={"provider_response_id": resp_id}
                                )
                            if span is not None:
                                usage_meta = response_usage(response)
                                if usage_meta.get("total_tokens"):
                                    span.set_usage(usage_meta)

                    if is_structured:
                        if parsing_error is not None:
                            attempt_reported = True
                            await self._report_attempt_guarded(
                                invocation=invocation,
                                usage=canon_usage,
                                status="failed",
                                finish_reason=finish_reason,
                                error_kind="structured_output_parse_error",
                            )
                            raise parsing_error

                        attempt_reported = True
                        await self._report_attempt_guarded(
                            invocation=invocation,
                            usage=canon_usage,
                            status="succeeded",
                            finish_reason=finish_reason,
                        )
                        candidate.circuit.succeed()
                        return response if self.caller_include_raw else parsed

                    attempt_reported = True
                    await self._report_attempt_guarded(
                        invocation=invocation,
                        usage=canon_usage,
                        status="succeeded",
                        finish_reason=finish_reason,
                    )
                    candidate.circuit.succeed()
                    return response
                except _ReporterFailure as failure:
                    raise failure.cause
                except asyncio.CancelledError:
                    if not attempt_reported:
                        await self._report_attempt(
                            invocation=invocation,
                            usage=CanonicalTokenUsage(source="none"),
                            status="cancelled",
                        )
                    raise
                except BaseException as error:
                    last_error = error
                    decision = classify_model_error(error)
                    candidate.circuit.fail(candidate.preset.circuit_breaker)
                    if not attempt_reported:
                        await self._report_attempt(
                            invocation=invocation,
                            usage=error_canonical_usage(error),
                            status="failed",
                            error_kind=decision.kind,
                        )
                    if not decision.retryable:
                        raise
                    if attempt < candidate.preset.retry.max_attempts:
                        delay = self._delay(candidate, attempt, decision)
                        global_telemetry.event(
                            "model.retry",
                            level="warning",
                            payload={
                                "provider": candidate.provider_name,
                                "model": candidate.definition.model_id,
                                "attempt": attempt,
                                "error_kind": decision.kind,
                                "delay_s": delay,
                            },
                        )
                        await asyncio.sleep(delay)
            if fallback_index + 1 < len(self.candidates):
                global_telemetry.event(
                    "model.failover",
                    level="warning",
                    payload={
                        "from_provider": candidate.provider_name,
                        "from_model": candidate.definition.model_id,
                        "to_model": self.candidates[
                            fallback_index + 1
                        ].definition.model_id,
                        "error_kind": (
                            classify_model_error(last_error).kind
                            if last_error
                            else "circuit_open"
                        ),
                    },
                )
        raise ModelRuntimeExhaustedError(
            "All configured model candidates failed"
        ) from last_error

    async def astream(
        self, input: Any, config: Any = None, **kwargs: Any
    ) -> AsyncIterator[AIMessageChunk]:
        last_error: BaseException | None = None
        for fallback_index, candidate in enumerate(self.candidates):
            if not candidate.circuit.available():
                continue
            for attempt in range(1, candidate.preset.retry.max_attempts + 1):
                invocation, operation_id = self._prepare_invocation(
                    candidate, attempt, fallback_index
                )
                received = False
                visible = False
                first = True
                started = time.monotonic()
                latest_usage: CanonicalTokenUsage = CanonicalTokenUsage(
                    source="none"
                )
                finish_reason: str | None = None
                provider_response_id: str | None = None
                try:
                    async with _attempt_span(
                        candidate, attempt, fallback_index, operation_id
                    ) as span:
                        iterator = candidate.model.astream(
                            input, config=config, **kwargs
                        ).__aiter__()
                        while True:
                            remaining_total = (
                                candidate.preset.timeouts.total_s
                                - (time.monotonic() - started)
                            )
                            if remaining_total <= 0:
                                raise asyncio.TimeoutError(
                                    "model stream total timeout"
                                )
                            wait_s = min(
                                remaining_total,
                                candidate.preset.timeouts.first_token_s
                                if first
                                else candidate.preset.timeouts.stream_idle_s,
                            )
                            try:
                                chunk = await asyncio.wait_for(
                                    iterator.__anext__(), timeout=wait_s
                                )
                            except StopAsyncIteration:
                                break
                            first = False
                            received = True
                            visible = visible or self._visible_chunk(chunk)
                            normalized = (
                                normalize_chunk(chunk)
                                if isinstance(chunk, AIMessageChunk)
                                else chunk
                            )
                            chunk_canon = response_canonical_usage(normalized)
                            if chunk_canon.source != "none":
                                latest_usage = chunk_canon
                            resp_id = extract_provider_response_id(normalized)
                            if resp_id:
                                provider_response_id = resp_id
                            finish = (
                                getattr(normalized, "response_metadata", {})
                                or {}
                            ).get("finish_reason")
                            if finish:
                                finish_reason = finish
                            if span is not None:
                                if "ttft_ms" not in span.attributes:
                                    span.annotate(
                                        ttft_ms=max(
                                            0,
                                            int(
                                                (time.monotonic() - started)
                                                * 1000
                                            ),
                                        )
                                    )
                                usage = response_usage(normalized)
                                if usage["total_tokens"]:
                                    span.set_usage(usage)
                            yield normalized
                        if not received:
                            raise EmptyModelResponseError(
                                "Provider stream completed without chunks"
                            )
                    if (
                        provider_response_id
                        and latest_usage.provider_response_id is None
                    ):
                        latest_usage = latest_usage.model_copy(
                            update={"provider_response_id": provider_response_id}
                        )
                    await self._report_attempt_guarded(
                        invocation=invocation,
                        usage=latest_usage,
                        status="succeeded",
                        finish_reason=finish_reason,
                    )
                    candidate.circuit.succeed()
                    return
                except _ReporterFailure as failure:
                    raise failure.cause
                except asyncio.CancelledError:
                    if (
                        provider_response_id
                        and latest_usage.provider_response_id is None
                    ):
                        latest_usage = latest_usage.model_copy(
                            update={"provider_response_id": provider_response_id}
                        )
                    await self._report_attempt(
                        invocation=invocation,
                        usage=latest_usage,
                        status="cancelled",
                        finish_reason=finish_reason,
                    )
                    raise
                except BaseException as error:
                    last_error = error
                    decision = classify_model_error(error)
                    candidate.circuit.fail(candidate.preset.circuit_breaker)
                    if (
                        provider_response_id
                        and latest_usage.provider_response_id is None
                    ):
                        latest_usage = latest_usage.model_copy(
                            update={"provider_response_id": provider_response_id}
                        )
                    if latest_usage.source == "none":
                        latest_usage = error_canonical_usage(error)
                    if visible:
                        global_telemetry.event(
                            "model.stream_interrupted",
                            level="error",
                            payload={
                                "provider": candidate.provider_name,
                                "model": candidate.definition.model_id,
                                "error_kind": decision.kind,
                            },
                        )
                        await self._report_attempt(
                            invocation=invocation,
                            usage=latest_usage,
                            status="interrupted",
                            finish_reason=finish_reason,
                            error_kind=decision.kind,
                        )
                        raise StreamInterruptedError(
                            "Model stream interrupted after visible output",
                            provider=candidate.provider_name,
                            model=candidate.definition.model_id,
                        ) from error
                    await self._report_attempt(
                        invocation=invocation,
                        usage=latest_usage,
                        status="failed",
                        finish_reason=finish_reason,
                        error_kind=decision.kind,
                    )
                    if not decision.retryable:
                        raise
                    if attempt < candidate.preset.retry.max_attempts:
                        await asyncio.sleep(
                            self._delay(candidate, attempt, decision)
                        )
            if fallback_index + 1 < len(self.candidates):
                global_telemetry.event(
                    "model.failover",
                    level="warning",
                    payload={
                        "from_model": candidate.definition.model_id,
                        "to_model": self.candidates[
                            fallback_index + 1
                        ].definition.model_id,
                        "streaming": True,
                    },
                )
        raise ModelRuntimeExhaustedError(
            "All configured streaming model candidates failed"
        ) from last_error
