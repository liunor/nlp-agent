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

from langchain_core.exceptions import OutputParserException
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
    response_billable_feature_usage,
    response_canonical_usage,
    response_usage,
)
from core.model_runtime.usage import (
    CanonicalTokenUsage,
    InvocationOutcome,
    InvocationStatus,
    ModelIdentity,
    ModelInvocation,
    UsageReporterUnavailableError,
    current_billable_feature_usage,
    resolve_usage_attribution,
)
from core.observability.context import current_telemetry_context
from core.observability.models import SpanKind, SpanStatus
from core.observability.runtime import global_telemetry
from utils.logger import get_logger


_FINISH_USAGE_DRAIN_S = 0.25
_STREAM_CANCEL_DRAIN_S = 0.25
logger = get_logger("nlp_agent.model_runtime")


def _with_response_feature_usage(
    invocation: ModelInvocation | None, message: Any
) -> ModelInvocation | None:
    if invocation is None:
        return None
    updates = response_billable_feature_usage(message)
    if not updates:
        return invocation
    current = invocation.feature_usage
    if updates.get("visual_input_tokens", 0) > 0:
        updates["image_units"] = 0
    return invocation.model_copy(
        update={"feature_usage": current.model_copy(update=updates)}
    )


class ModelRuntimeExhaustedError(RuntimeError):
    pass


class EmptyModelResponseError(RuntimeError):
    pass


class StructuredOutputParseError(ValueError):
    """A provider responded, but its structured payload failed local parsing."""


class ModelFinishReasonError(RuntimeError):
    """A provider reported an error through an otherwise successful response."""

    def __init__(
        self,
        *,
        finish_reason: str,
        error_kind: str,
        provider: str,
        model: str,
    ) -> None:
        super().__init__(
            f"Provider {provider!r} model {model!r} ended with "
            f"error finish_reason {finish_reason!r}"
        )
        self.finish_reason = finish_reason
        self.error_kind = error_kind
        self.provider = provider
        self.model = model


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
    error_finish_reasons: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.error_finish_reasons:
            self.error_finish_reasons = dict(
                getattr(self.model, "ERROR_FINISH_REASONS", {}) or {}
            )


@dataclass(frozen=True)
class ErrorDecision:
    retryable: bool
    kind: str
    retry_after_s: float | None = None


def _merge_delta_usage(
    current: CanonicalTokenUsage | None,
    incoming: CanonicalTokenUsage,
) -> CanonicalTokenUsage:
    """Add provider-reported per-chunk deltas without double-counting them."""
    if current is None or current.source == "none":
        return incoming.model_copy(update={"semantics": "delta"})
    return CanonicalTokenUsage(
        input_tokens=current.input_tokens + incoming.input_tokens,
        cached_input_tokens=current.cached_input_tokens + incoming.cached_input_tokens,
        cache_miss_input_tokens=current.cache_miss_input_tokens
        + incoming.cache_miss_input_tokens,
        cache_write_input_tokens=current.cache_write_input_tokens
        + incoming.cache_write_input_tokens,
        output_tokens=current.output_tokens + incoming.output_tokens,
        reasoning_output_tokens=current.reasoning_output_tokens
        + incoming.reasoning_output_tokens,
        total_tokens=current.total_tokens + incoming.total_tokens,
        source="provider",
        semantics="delta",
        provider_response_id=incoming.provider_response_id
        or current.provider_response_id,
    )


def _finalize_stream_usage(
    latest: CanonicalTokenUsage,
    delta: CanonicalTokenUsage | None,
    provider_response_id: str | None,
    *,
    complete: bool,
) -> CanonicalTokenUsage:
    usage = delta if delta is not None else latest
    updates: dict[str, Any] = {
        "semantics": "final" if complete else "partial",
    }
    if provider_response_id and usage.provider_response_id is None:
        updates["provider_response_id"] = provider_response_id
    return usage.model_copy(update=updates)


def classify_model_error(error: BaseException) -> ErrorDecision:
    if isinstance(error, (StructuredOutputParseError, OutputParserException)):
        return ErrorDecision(False, "structured_output_parse_error")
    if isinstance(error, EmptyModelResponseError):
        return ErrorDecision(True, "upstream_empty_response")
    if isinstance(error, ModelFinishReasonError):
        return ErrorDecision(
            error.error_kind == "upstream_overloaded",
            error.error_kind,
        )
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return ErrorDecision(True, "upstream_timeout")
    status = getattr(error, "status_code", None)
    message = str(error).lower()
    code = str(getattr(error, "code", "") or "").strip().lower()
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        details = body.get("error", body)
        if isinstance(details, dict):
            code = str(details.get("code", code) or code).strip().lower()
            message = (
                f"{message} {details.get('type', '')} {details.get('message', '')}"
            ).lower()

    retry_after = None
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        try:
            retry_after = max(
                0.0,
                float(headers.get("retry-after") or headers.get("Retry-After")),
            )
        except (TypeError, ValueError):
            retry_after = None

    if code == "1113":
        return ErrorDecision(False, "upstream_provider_quota_exhausted")
    if code == "1261":
        return ErrorDecision(False, "upstream_context_length_exceeded")
    if code == "1301":
        return ErrorDecision(False, "upstream_unknown")
    if code in {"1210", "1212", "1213", "1214", "1215"}:
        return ErrorDecision(False, "upstream_invalid_request")
    if code in {"1211", "1221", "1222"}:
        return ErrorDecision(False, "upstream_model_unavailable")
    if code == "1302":
        return ErrorDecision(True, "upstream_rate_limited", retry_after)
    if code == "1305":
        return ErrorDecision(True, "upstream_overloaded", retry_after)
    if code in {
        "1308",
        "1309",
        "1310",
        "1311",
        "1313",
        "1314",
        "1315",
        "1316",
        "1317",
        "1318",
        "1319",
        "1320",
        "1321",
    }:
        return ErrorDecision(False, "upstream_provider_quota_exhausted")

    quota_markers = (
        "allocationquota.freetieronly",
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
        self._abandoned_stream_tasks: set[asyncio.Task[Any]] = set()
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
                    error_finish_reasons=item.error_finish_reasons,
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
                    error_finish_reasons=item.error_finish_reasons,
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
            if self.reporter_slot is not None and getattr(
                self.reporter_slot, "required", False
            ):
                raise UsageReporterUnavailableError(
                    "This model process requires a configured usage Reporter"
                )
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

    async def _reserve_feature_attempt(
        self, invocation: ModelInvocation | None
    ) -> None:
        """Reserve known feature units before entering the Provider boundary."""

        if invocation is None or not any(invocation.feature_usage.model_dump().values()):
            return
        reporter = (
            self.reporter_slot.reporter
            if self.reporter_slot is not None
            else None
        )
        reserve = getattr(reporter, "reserve_feature_usage", None)
        if reserve is None:
            if self.reporter_slot is not None and getattr(
                self.reporter_slot, "required", False
            ):
                raise UsageReporterUnavailableError(
                    "Required usage Reporter cannot reserve billable feature usage"
                )
            return
        await reserve(invocation)

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
        if (
            self.reporter_slot is not None
            and getattr(self.reporter_slot, "required", False)
            and not has_reporter
        ):
            raise UsageReporterUnavailableError(
                "This model process requires a configured usage Reporter"
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
        feature_usage = current_billable_feature_usage()
        if candidate.preset.native_search.enabled and candidate.preset.native_search.forced:
            feature_usage = feature_usage.model_copy(
                update={"search_calls": feature_usage.search_calls + 1}
            )
        invocation = ModelInvocation(
            operation_id=operation_id,
            identity=identity,
            attribution=attribution,
            attempt=attempt,
            fallback_index=fallback_index,
            started_at=started_at,
            feature_usage=feature_usage,
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

    @staticmethod
    def _finish_reason_error(
        candidate: ModelCandidate, finish_reason: str | None
    ) -> ModelFinishReasonError | None:
        if not finish_reason:
            return None
        error_kind = candidate.error_finish_reasons.get(finish_reason)
        if error_kind is None:
            return None
        return ModelFinishReasonError(
            finish_reason=finish_reason,
            error_kind=error_kind,
            provider=candidate.provider_name,
            model=candidate.definition.model_id,
        )

    async def _read_stream_chunk(
        self, iterator: Any, timeout_s: float, *, task_name: str
    ) -> Any:
        read_task = asyncio.ensure_future(iterator.__anext__())
        read_task.set_name(task_name)
        timeout_task = asyncio.create_task(
            asyncio.sleep(timeout_s), name=f"{task_name}:timeout"
        )
        try:
            done, _pending = await asyncio.wait(
                {read_task, timeout_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            timeout_task.cancel()
            await asyncio.gather(timeout_task, return_exceptions=True)
            await self._cancel_stream_task(read_task)
            raise

        if read_task in done:
            timeout_task.cancel()
            await asyncio.gather(timeout_task, return_exceptions=True)
            return await read_task

        await self._cancel_stream_task(read_task)
        raise asyncio.TimeoutError

    async def _cancel_stream_task(self, task: asyncio.Task[Any]) -> None:
        if task.done():
            self._consume_stream_task(task)
            return
        task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.shield(task), timeout=_STREAM_CANCEL_DRAIN_S
            )
        except asyncio.TimeoutError:
            self._detach_stream_task(task)
        except asyncio.CancelledError:
            # A normally cancelled child task raises CancelledError through
            # shield(); that is expected and must not abort the caller.  If
            # the caller itself was cancelled while the child ignored its
            # cancellation, detach it and preserve the caller cancellation.
            if task.done():
                self._consume_stream_task(task)
                return
            self._detach_stream_task(task)
            raise
        except BaseException:
            self._consume_stream_task(task)

    def _detach_stream_task(self, task: asyncio.Task[Any]) -> None:
        self._abandoned_stream_tasks.add(task)
        task.add_done_callback(self._consume_stream_task)

    def _consume_stream_task(self, task: asyncio.Task[Any]) -> None:
        self._abandoned_stream_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.exception()
        except asyncio.CancelledError:
            return

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
                await self._reserve_feature_attempt(invocation)
                attempt_reported = False
                terminal_error: ModelFinishReasonError | None = None
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
                            invocation = _with_response_feature_usage(
                                invocation, raw_msg
                            )
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
                            invocation = _with_response_feature_usage(
                                invocation, response
                            )
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

                        terminal_error = self._finish_reason_error(
                            candidate, finish_reason
                        )
                        if terminal_error is not None and span is not None:
                            span.set_status(
                                SpanStatus.ERROR,
                                error_kind=terminal_error.error_kind,
                                error_message=str(terminal_error),
                            )

                    if terminal_error is not None:
                        attempt_reported = True
                        await self._report_attempt_guarded(
                            invocation=invocation,
                            usage=canon_usage,
                            status="failed",
                            finish_reason=finish_reason,
                            error_kind=terminal_error.error_kind,
                        )
                        raise terminal_error

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
                            raise StructuredOutputParseError(str(parsing_error)) from parsing_error

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
                    if decision.kind != "structured_output_parse_error":
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
                await self._reserve_feature_attempt(invocation)
                received = False
                visible = False
                first = True
                started = time.monotonic()
                latest_usage: CanonicalTokenUsage = CanonicalTokenUsage(
                    source="none"
                )
                delta_usage: CanonicalTokenUsage | None = None
                finish_reason: str | None = None
                finish_drain_deadline: float | None = None
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
                            if finish_drain_deadline is not None:
                                remaining_finish_drain = (
                                    finish_drain_deadline - time.monotonic()
                                )
                                if remaining_finish_drain <= 0:
                                    break
                            else:
                                remaining_finish_drain = remaining_total
                            wait_s = min(
                                remaining_total,
                                remaining_finish_drain,
                                candidate.preset.timeouts.first_token_s
                                if first
                                else candidate.preset.timeouts.stream_idle_s,
                            )
                            try:
                                chunk = await self._read_stream_chunk(
                                    iterator,
                                    wait_s,
                                    task_name=(
                                        f"model-stream-read:{candidate.provider_name}:"
                                        f"{candidate.definition.model_id}"
                                    ),
                                )
                            except StopAsyncIteration:
                                break
                            except asyncio.TimeoutError:
                                if finish_drain_deadline is not None:
                                    break
                                raise
                            first = False
                            received = True
                            chunk_visible = self._visible_chunk(chunk)
                            visible = visible or chunk_visible
                            normalized = (
                                normalize_chunk(chunk)
                                if isinstance(chunk, AIMessageChunk)
                                else chunk
                            )
                            chunk_canon = response_canonical_usage(normalized)
                            invocation = _with_response_feature_usage(
                                invocation, normalized
                            )
                            if chunk_canon.source != "none":
                                if chunk_canon.semantics == "delta":
                                    delta_usage = _merge_delta_usage(
                                        delta_usage, chunk_canon
                                    )
                                else:
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
                            if span is not None and chunk_visible:
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
                            if finish and finish_drain_deadline is None:
                                finish_drain_deadline = (
                                    time.monotonic() + _FINISH_USAGE_DRAIN_S
                                )
                        if not received:
                            raise EmptyModelResponseError(
                                "Provider stream completed without chunks"
                            )
                        terminal_error = self._finish_reason_error(
                            candidate, finish_reason
                        )
                        if terminal_error is not None:
                            raise terminal_error
                    final_usage = _finalize_stream_usage(
                        latest_usage,
                        delta_usage,
                        provider_response_id,
                        complete=True,
                    )
                    await self._report_attempt_guarded(
                        invocation=invocation,
                        usage=final_usage,
                        status="succeeded",
                        finish_reason=finish_reason,
                    )
                    candidate.circuit.succeed()
                    return
                except _ReporterFailure as failure:
                    raise failure.cause
                except asyncio.CancelledError:
                    partial_usage = _finalize_stream_usage(
                        latest_usage,
                        delta_usage,
                        provider_response_id,
                        complete=False,
                    )
                    await self._report_attempt(
                        invocation=invocation,
                        usage=partial_usage,
                        status="cancelled",
                        finish_reason=finish_reason,
                    )
                    raise
                except BaseException as error:
                    last_error = error
                    decision = classify_model_error(error)
                    if decision.kind != "structured_output_parse_error":
                        candidate.circuit.fail(candidate.preset.circuit_breaker)
                    partial_usage = _finalize_stream_usage(
                        latest_usage,
                        delta_usage,
                        provider_response_id,
                        complete=False,
                    )
                    if partial_usage.source == "none":
                        partial_usage = error_canonical_usage(error)
                        if (
                            provider_response_id
                            and partial_usage.provider_response_id is None
                        ):
                            partial_usage = partial_usage.model_copy(
                                update={"provider_response_id": provider_response_id}
                            )
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
                            usage=partial_usage,
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
                        usage=partial_usage,
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
