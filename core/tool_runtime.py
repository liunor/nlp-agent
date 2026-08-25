"""Unified, policy-governed tool catalog and execution runtime."""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import AsyncExitStack
from enum import Enum
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from utils.logger import get_logger
from core.tool_safety import (
    ToolAuditEvent,
    ToolAuditLog,
    ToolAuthorizationManager,
    global_tool_audit_log,
    global_tool_authorizations,
)


logger = get_logger("nlp_agent.tool_runtime")
_TOOL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
_TOOL_CATEGORY = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class ToolSource(str, Enum):
    BUILTIN = "builtin"
    CUSTOM = "custom"
    MCP = "mcp"
    ORCHESTRATION = "orchestration"


class ToolScope(str, Enum):
    COORDINATOR = "coordinator"
    WORKER = "worker"


class ToolRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ToolLockScope(str, Enum):
    NONE = "none"
    SESSION = "session"
    GLOBAL = "global"


class ToolRetryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_attempts: int = Field(default=1, ge=1, le=5)
    retryable_kinds: frozenset[str] = Field(
        default_factory=lambda: frozenset({"timeout", "network", "rate_limit"})
    )
    base_delay_s: float = Field(default=0.25, ge=0, le=30)
    max_delay_s: float = Field(default=4.0, ge=0, le=120)
    jitter_ratio: float = Field(default=0.20, ge=0, le=1)

    @field_validator("retryable_kinds")
    @classmethod
    def validate_retryable_kinds(cls, values: frozenset[str]) -> frozenset[str]:
        supported = {"timeout", "network", "rate_limit"}
        unknown = values.difference(supported)
        if unknown:
            raise ValueError(f"unsupported retry kinds: {', '.join(sorted(unknown))}")
        return values

    @model_validator(mode="after")
    def validate_delays(self) -> "ToolRetryPolicy":
        if self.max_delay_s < self.base_delay_s:
            raise ValueError("max_delay_s must be greater than or equal to base_delay_s")
        return self

    def delay_for(self, failed_attempt: int) -> float:
        delay = self.base_delay_s * (2 ** max(0, failed_attempt - 1))
        if delay and self.jitter_ratio:
            delay *= random.uniform(1 - self.jitter_ratio, 1 + self.jitter_ratio)
        return min(self.max_delay_s, max(0, delay))


class ToolDescriptor(BaseModel):
    """Pydantic-v2 validated metadata plus a factory for one executable tool."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    name: str
    description: str
    source: ToolSource
    provider: str = "core"
    provider_id: str = ""
    version: str = "1.0"
    category: str = "general"
    prompt_priority: int = Field(default=100, ge=-1000, le=1000)
    scopes: frozenset[ToolScope]
    capabilities: frozenset[str] = Field(default_factory=frozenset)
    risk: ToolRisk = ToolRisk.LOW
    read_only: bool = False
    idempotent: bool = False
    concurrency_safe: bool = False
    exclusive: bool = False
    lock_scope: ToolLockScope = ToolLockScope.NONE
    timeout_s: float = Field(default=30.0, gt=0, le=1800)
    max_concurrency: int = Field(default=0, ge=0, le=100)
    retry: ToolRetryPolicy = Field(default_factory=ToolRetryPolicy)
    persist_result: bool = True
    enabled: bool = True
    factory: Callable[[], BaseTool] = Field(exclude=True, repr=False)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _TOOL_NAME.fullmatch(value):
            raise ValueError(
                "tool name must start with a letter/underscore, contain only "
                "letters, digits, underscore or hyphen, and be at most 64 characters"
            )
        return value

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, values: frozenset[str]) -> frozenset[str]:
        for value in values:
            if not value or value.strip() != value or " " in value:
                raise ValueError(f"invalid capability: {value!r}")
        return values

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("tool version cannot be blank")
        return value

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        if not _TOOL_CATEGORY.fullmatch(value):
            raise ValueError("tool category must be lowercase letters, digits, _ or -")
        return value

    @model_validator(mode="after")
    def validate_concurrency_contract(self) -> "ToolDescriptor":
        if self.source == ToolSource.CUSTOM and self.category == "nlp" and not self.name.startswith("nlp_"):
            raise ValueError("NLP custom tools must use the nlp_ namespace")
        if self.source == ToolSource.MCP and not self.name.startswith("mcp_"):
            raise ValueError("MCP tools must use the mcp_ namespace")
        if self.exclusive and self.concurrency_safe:
            raise ValueError("exclusive tools cannot be concurrency_safe")
        if self.exclusive and self.lock_scope == ToolLockScope.NONE:
            object.__setattr__(self, "lock_scope", ToolLockScope.GLOBAL)
        if self.lock_scope != ToolLockScope.NONE and self.concurrency_safe:
            raise ValueError("locked tools cannot be concurrency_safe")
        if not self.read_only and self.concurrency_safe:
            raise ValueError("only read-only tools may be marked concurrency_safe")
        if self.retry.max_attempts > 1 and not (self.read_only or self.idempotent):
            raise ValueError("retries require a read-only or explicitly idempotent tool")
        return self

    def instantiate(self) -> BaseTool:
        tool = self.factory()
        if tool.name != self.name:
            raise ValueError(
                f"tool factory for {self.name!r} produced mismatched name {tool.name!r}"
            )
        return tool


class ToolGrantRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: ToolScope
    session_id: str = ""
    profile: str = ""
    allowed_tools: frozenset[str] = Field(default_factory=frozenset)
    allowed_capabilities: frozenset[str] = Field(default_factory=frozenset)
    denied_tools: frozenset[str] = Field(default_factory=frozenset)
    denied_capabilities: frozenset[str] = Field(default_factory=frozenset)
    allow_high_risk: bool = False


class ToolGrantSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_version: str = "1"
    role: ToolScope
    session_id: str = ""
    profile: str = ""
    granted_tools: tuple[str, ...]
    granted_capabilities: tuple[str, ...]
    catalog_revision: int
    created_at: float = Field(default_factory=time.time)


class ToolExecutionError(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal[
        "not_found",
        "permission_denied",
        "validation",
        "timeout",
        "execution",
        "network",
        "rate_limit",
        "tool_error",
    ]
    message: str
    code: str = Field(default="", max_length=128, pattern=r"^[A-Za-z0-9_.:-]*$")
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False

    @field_validator("details", mode="before")
    @classmethod
    def validate_safe_details(cls, value: Any) -> dict[str, Any]:
        """Keep only bounded JSON metadata explicitly marked safe by the tool."""
        if not isinstance(value, Mapping):
            return {}

        def sanitize(item: Any, *, depth: int) -> Any:
            if item is None or isinstance(item, (bool, int, float)):
                return item
            if isinstance(item, str):
                return item[:1_000]
            if depth >= 3:
                return None
            if isinstance(item, Mapping):
                output: dict[str, Any] = {}
                for key, nested in list(item.items())[:32]:
                    if not isinstance(key, str) or not key or len(key) > 64:
                        continue
                    sanitized = sanitize(nested, depth=depth + 1)
                    if sanitized is not None:
                        output[key] = sanitized
                return output
            if isinstance(item, (list, tuple)):
                return [
                    sanitized
                    for nested in list(item)[:32]
                    if (sanitized := sanitize(nested, depth=depth + 1)) is not None
                ]
            return None

        sanitized = sanitize(value, depth=0)
        return sanitized if isinstance(sanitized, dict) else {}


class ToolExecutionResult(BaseModel):
    tool_name: str
    ok: bool
    output: Any = None
    error: ToolExecutionError | None = None
    duration_ms: int = Field(default=0, ge=0)
    attempts: int = Field(default=1, ge=0)

    @model_validator(mode="after")
    def validate_status(self) -> "ToolExecutionResult":
        if self.ok and self.error is not None:
            raise ValueError("successful result cannot contain an error")
        if not self.ok and self.error is None:
            raise ValueError("failed result must contain an error")
        return self

    def to_model_content(self, *, max_chars: int | None = None) -> str:
        if self.ok:
            if isinstance(self.output, str):
                content = self.output
            else:
                content = json.dumps(self.output, ensure_ascii=False, default=str)
        else:
            content = json.dumps(
                {
                    "ok": False,
                    "tool": self.tool_name,
                    "error": self.error.model_dump() if self.error else None,
                },
                ensure_ascii=False,
            )
        if max_chars is None:
            return content
        from core.agent_runtime import compact_model_content

        return compact_model_content(content, max_chars)


class ToolCatalog:
    """Single source of truth for tool definitions and collision policy."""

    def __init__(self) -> None:
        self._descriptors: dict[str, ToolDescriptor] = {}
        self.revision = 0

    def register(self, descriptor: ToolDescriptor, *, replace: bool = False) -> None:
        existing = self._descriptors.get(descriptor.name)
        if existing is not None and not replace:
            raise ValueError(
                f"tool name collision: {descriptor.name!r} is already provided by "
                f"{existing.source.value}:{existing.provider}"
            )
        self._descriptors[descriptor.name] = descriptor
        self.revision += 1
        logger.info(
            "Tool registered",
            tool_name=descriptor.name,
            source=descriptor.source.value,
            provider=descriptor.provider,
        )

    def unregister(self, name: str) -> None:
        if self._descriptors.pop(name, None) is not None:
            self.revision += 1

    def unregister_provider(self, source: ToolSource, provider: str) -> int:
        names = [
            name
            for name, descriptor in self._descriptors.items()
            if descriptor.source == source and descriptor.provider == provider
        ]
        for name in names:
            self._descriptors.pop(name, None)
        if names:
            self.revision += 1
        return len(names)

    def get(self, name: str) -> ToolDescriptor | None:
        return self._descriptors.get(name)

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        source_order = {
            ToolSource.ORCHESTRATION: 0,
            ToolSource.BUILTIN: 1,
            ToolSource.CUSTOM: 2,
            ToolSource.MCP: 3,
        }
        return tuple(
            sorted(
                self._descriptors.values(),
                key=lambda item: (-item.prompt_priority, source_order[item.source], item.name),
            )
        )

    def names(self) -> tuple[str, ...]:
        return tuple(descriptor.name for descriptor in self.descriptors())


class ToolPolicyResolver:
    """Resolve explicit tool/capability requests into an immutable grant."""

    def __init__(
        self,
        catalog: ToolCatalog,
        authorization: ToolAuthorizationManager,
        *,
        policy_version: str = "2",
    ) -> None:
        self.catalog = catalog
        self.authorization = authorization
        self.policy_version = policy_version

    def resolve(self, request: ToolGrantRequest) -> ToolGrantSnapshot:
        unknown = request.allowed_tools.difference(self.catalog.names())
        if unknown:
            raise ValueError(f"unknown tools requested: {', '.join(sorted(unknown))}")

        granted: list[ToolDescriptor] = []
        for descriptor in self.catalog.descriptors():
            if not descriptor.enabled or request.role not in descriptor.scopes:
                continue
            explicitly_named = descriptor.name in request.allowed_tools
            capability_match = bool(
                descriptor.capabilities.intersection(request.allowed_capabilities)
            )
            if not explicitly_named and not capability_match:
                continue
            if descriptor.name in request.denied_tools:
                continue
            if descriptor.capabilities.intersection(request.denied_capabilities):
                continue
            if descriptor.risk in {ToolRisk.HIGH, ToolRisk.CRITICAL} and not (
                request.allow_high_risk
                and self.authorization.is_granted(request.session_id, descriptor.name)
            ):
                continue
            granted.append(descriptor)

        granted_capabilities = sorted(
            set().union(*(descriptor.capabilities for descriptor in granted)) if granted else set()
        )
        return ToolGrantSnapshot(
            policy_version=self.policy_version,
            role=request.role,
            session_id=request.session_id,
            profile=request.profile,
            granted_tools=tuple(descriptor.name for descriptor in granted),
            granted_capabilities=tuple(granted_capabilities),
            catalog_revision=self.catalog.revision,
        )


class ToolExecutor:
    """Pydantic-v2 validation, timeouts, error normalization, and telemetry."""

    def __init__(
        self,
        authorization: ToolAuthorizationManager = global_tool_authorizations,
        audit_log: ToolAuditLog = global_tool_audit_log,
    ) -> None:
        self.authorization = authorization
        self.audit_log = audit_log
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._global_locks: dict[str, asyncio.Lock] = {}
        self._session_locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def execute(
        self,
        descriptor: ToolDescriptor,
        tool: BaseTool,
        arguments: Mapping[str, Any] | None,
        grant: ToolGrantSnapshot,
        config: RunnableConfig | None = None,
    ) -> ToolExecutionResult:
        started = time.monotonic()
        argument_keys = tuple(sorted(str(key) for key in (arguments or {})))
        if descriptor.risk in {ToolRisk.HIGH, ToolRisk.CRITICAL} and not self.authorization.is_granted(
            grant.session_id, descriptor.name
        ):
            result = self._failure(
                descriptor.name,
                started,
                "permission_denied",
                "high-risk tool requires an active session grant",
                attempts=0,
            )
            await self._audit(
                descriptor,
                grant,
                phase="denied",
                outcome="denied",
                error_kind="permission_denied",
                attempt=0,
                argument_keys=argument_keys,
            )
            return result
        try:
            params = self._validate_arguments(tool, dict(arguments or {}))
        except Exception as error:
            result = self._failure(
                descriptor.name, started, "validation", str(error), attempts=0
            )
            await self._audit(
                descriptor,
                grant,
                phase="completed",
                outcome="error",
                error_kind="validation",
                attempt=0,
                argument_keys=argument_keys,
            )
            return result

        async def invoke() -> Any:
            return await tool.ainvoke(params, config=config)

        async with AsyncExitStack() as stack:
            semaphore = self._semaphore_for(descriptor)
            if semaphore is not None:
                await stack.enter_async_context(semaphore)
            lock = self._exclusive_lock(descriptor, grant.session_id)
            if lock is not None:
                await stack.enter_async_context(lock)

            final: ToolExecutionResult | None = None
            for attempt in range(1, descriptor.retry.max_attempts + 1):
                await self._audit(
                    descriptor,
                    grant,
                    phase="attempt",
                    outcome="started",
                    attempt=attempt,
                    argument_keys=argument_keys,
                )
                try:
                    output = await asyncio.wait_for(invoke(), timeout=descriptor.timeout_s)
                    tool_error = self._detect_tool_error(output)
                    if tool_error is None:
                        final = ToolExecutionResult(
                            tool_name=descriptor.name,
                            ok=True,
                            output=output,
                            duration_ms=int((time.monotonic() - started) * 1000),
                            attempts=attempt,
                        )
                    else:
                        final = self._failure(
                            descriptor.name,
                            started,
                            "tool_error",
                            tool_error.message,
                            code=tool_error.code,
                            details=tool_error.details,
                            attempts=attempt,
                        )
                except asyncio.CancelledError:
                    raise
                except asyncio.TimeoutError:
                    can_retry = descriptor.read_only or descriptor.idempotent
                    final = self._failure(
                        descriptor.name,
                        started,
                        "timeout",
                        f"tool timed out after {descriptor.timeout_s:g} seconds",
                        retryable=can_retry and "timeout" in descriptor.retry.retryable_kinds,
                        attempts=attempt,
                    )
                except Exception as error:
                    kind, retryable = self._classify_exception(error)
                    retryable = bool(
                        retryable
                        and (descriptor.read_only or descriptor.idempotent)
                        and kind in descriptor.retry.retryable_kinds
                    )
                    final = self._failure(
                        descriptor.name,
                        started,
                        kind,
                        f"{type(error).__name__}: {error}",
                        retryable=retryable,
                        attempts=attempt,
                    )

                if final.ok or not self._should_retry(descriptor, final, attempt):
                    break
                await self._audit(
                    descriptor,
                    grant,
                    phase="retry",
                    outcome="error",
                    error_kind=final.error.kind if final.error else "execution",
                    attempt=attempt,
                    argument_keys=argument_keys,
                )
                await asyncio.sleep(descriptor.retry.delay_for(attempt))

        assert final is not None
        await self._audit(
            descriptor,
            grant,
            phase="completed",
            outcome="success" if final.ok else "error",
            error_kind=final.error.kind if final.error else "",
            attempt=final.attempts,
            duration_ms=final.duration_ms,
            argument_keys=argument_keys,
        )
        return final

    @staticmethod
    def _validate_arguments(tool: BaseTool, arguments: dict[str, Any]) -> dict[str, Any]:
        schema = getattr(tool, "args_schema", None)
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            return schema.model_validate(arguments).model_dump(exclude_unset=True)
        return arguments

    @staticmethod
    def _detect_tool_error(output: Any) -> ToolExecutionError | None:
        if isinstance(output, Mapping):
            return ToolExecutor._structured_tool_error(output)
        if not isinstance(output, str):
            return None
        stripped = output.strip()
        if stripped.startswith("Error:") or stripped.startswith("错误："):
            return ToolExecutionError(kind="tool_error", message=stripped)
        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
            except Exception:
                return None
            if isinstance(payload, dict):
                return ToolExecutor._structured_tool_error(payload)
        return None

    @staticmethod
    def _structured_tool_error(payload: Mapping[str, Any]) -> ToolExecutionError | None:
        raw_error = payload.get("error")
        if not raw_error:
            return None

        raw_code = payload.get("code", "")
        raw_details = payload.get("details", {})
        if isinstance(raw_error, Mapping):
            message_value = (
                raw_error.get("message")
                or raw_error.get("error")
                or raw_error.get("detail")
            )
            message = (
                str(message_value)
                if message_value is not None
                else json.dumps(raw_error, ensure_ascii=False, default=str)
            )
            raw_code = raw_error.get("code") or raw_code
            raw_details = raw_error.get("details", raw_details)
        else:
            message = str(raw_error)

        code = str(raw_code) if raw_code is not None else ""
        if len(code) > 128 or re.fullmatch(r"[A-Za-z0-9_.:-]*", code) is None:
            code = ""
        return ToolExecutionError(
            kind="tool_error",
            message=message,
            code=code,
            details=raw_details if isinstance(raw_details, Mapping) else {},
        )

    def _semaphore_for(self, descriptor: ToolDescriptor) -> asyncio.Semaphore | None:
        if descriptor.max_concurrency <= 0:
            return None
        return self._semaphores.setdefault(
            descriptor.name, asyncio.Semaphore(descriptor.max_concurrency)
        )

    def _exclusive_lock(
        self, descriptor: ToolDescriptor, session_id: str
    ) -> asyncio.Lock | None:
        if descriptor.lock_scope == ToolLockScope.GLOBAL:
            return self._global_locks.setdefault(descriptor.name, asyncio.Lock())
        if descriptor.lock_scope == ToolLockScope.SESSION:
            return self._session_locks.setdefault(
                (session_id, descriptor.name), asyncio.Lock()
            )
        return None

    @staticmethod
    def _classify_exception(error: Exception) -> tuple[str, bool]:
        text = f"{type(error).__name__}: {error}".lower()
        status = getattr(error, "status_code", None)
        if status == 429 or "rate limit" in text or "ratelimit" in text:
            return "rate_limit", True
        if isinstance(error, ConnectionError) or any(
            marker in text
            for marker in ("connection", "network", "temporarily unavailable", "dns")
        ):
            return "network", True
        return "execution", False

    @staticmethod
    def _should_retry(
        descriptor: ToolDescriptor,
        result: ToolExecutionResult,
        attempt: int,
    ) -> bool:
        return bool(
            not result.ok
            and result.error
            and result.error.retryable
            and result.error.kind in descriptor.retry.retryable_kinds
            and attempt < descriptor.retry.max_attempts
            and (descriptor.read_only or descriptor.idempotent)
        )

    async def _audit(
        self,
        descriptor: ToolDescriptor,
        grant: ToolGrantSnapshot,
        *,
        phase: str,
        outcome: str,
        attempt: int,
        error_kind: str = "",
        duration_ms: int = 0,
        argument_keys: tuple[str, ...] = (),
    ) -> None:
        try:
            from core.observability.context import current_telemetry_context

            telemetry = current_telemetry_context()
            await self.audit_log.emit(
                ToolAuditEvent(
                    session_id=grant.session_id,
                    trace_id=telemetry.trace_id if telemetry else "",
                    span_id=telemetry.span_id if telemetry else "",
                    turn_id=telemetry.turn_id if telemetry else "",
                    worker_id=telemetry.worker_id if telemetry and telemetry.worker_id else "",
                    role=grant.role.value,
                    profile=grant.profile,
                    tool_name=descriptor.name,
                    provider=descriptor.provider,
                    phase=phase,
                    attempt=attempt,
                    outcome=outcome,
                    error_kind=error_kind,
                    duration_ms=duration_ms,
                    argument_keys=argument_keys,
                )
            )
        except Exception as error:
            logger.warning("Tool audit write failed", error=str(error))

    @staticmethod
    def _failure(
        tool_name: str,
        started: float,
        kind: str,
        message: str,
        *,
        code: str = "",
        details: Mapping[str, Any] | None = None,
        retryable: bool = False,
        attempts: int = 1,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name=tool_name,
            ok=False,
            error=ToolExecutionError(
                kind=kind,
                message=message,
                code=code,
                details=dict(details or {}),
                retryable=retryable,
            ),
            duration_ms=int((time.monotonic() - started) * 1000),
            attempts=attempts,
        )


class ToolSet:
    """One immutable grant used by both model binding and actual execution."""

    def __init__(
        self,
        snapshot: ToolGrantSnapshot,
        descriptors: Iterable[ToolDescriptor],
        executor: ToolExecutor,
    ) -> None:
        self.snapshot = snapshot
        self._descriptors = tuple(descriptors)
        self._descriptor_by_name = {item.name: item for item in self._descriptors}
        self._tools = {item.name: item.instantiate() for item in self._descriptors}
        self.executor = executor

    @property
    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return self._descriptors

    @property
    def tools(self) -> list[BaseTool]:
        return [self._tools[name] for name in self.snapshot.granted_tools]

    @property
    def names(self) -> tuple[str, ...]:
        return self.snapshot.granted_tools

    def has(self, name: str) -> bool:
        return name in self._tools

    def descriptor(self, name: str) -> ToolDescriptor | None:
        """Return immutable metadata for one granted tool, if present."""
        return self._descriptor_by_name.get(name)

    async def execute(
        self,
        name: str,
        arguments: Mapping[str, Any] | None,
        config: RunnableConfig | None = None,
    ) -> ToolExecutionResult:
        from core.observability.context import current_telemetry_context
        from core.observability.models import SpanKind, SpanStatus
        from core.observability.runtime import global_telemetry

        descriptor = self._descriptor_by_name.get(name)
        tool = self._tools.get(name)
        context = current_telemetry_context()
        if descriptor is None or tool is None:
            result = ToolExecutionResult(
                tool_name=name,
                ok=False,
                error=ToolExecutionError(
                    kind="permission_denied",
                    message=f"tool {name!r} is not granted in this runtime",
                ),
                attempts=0,
            )
            if context is None:
                return result
            async with global_telemetry.span(
                SpanKind.TOOL, f"tool.{name}", context=context,
                attributes={"tool_name": name, "argument_keys": sorted((arguments or {}).keys())},
            ) as span:
                span.set_status(
                    SpanStatus.DENIED, error_kind="permission_denied",
                    error_message=result.error.message if result.error else None,
                )
                return result
        if context is None:
            return await self.executor.execute(
                descriptor, tool, arguments, self.snapshot, config
            )
        async with global_telemetry.span(
            SpanKind.TOOL,
            f"tool.{name}",
            context=context,
            attributes={
                "tool_name": name,
                "argument_keys": sorted((arguments or {}).keys()),
                "source": descriptor.source.value,
                "risk": descriptor.risk.value,
            },
        ) as span:
            result = await self.executor.execute(
                descriptor, tool, arguments, self.snapshot, config
            )
            span.annotate(attempts=result.attempts, runtime_duration_ms=result.duration_ms)
            if not result.ok:
                kind = result.error.kind if result.error else "tool_error"
                status = SpanStatus.TIMEOUT if kind == "timeout" else (
                    SpanStatus.DENIED if kind == "permission_denied" else SpanStatus.ERROR
                )
                span.set_status(
                    status,
                    error_kind=kind,
                    error_message=result.error.message if result.error else None,
                )
            return result

    async def execute_many(
        self,
        calls: list[tuple[str, Mapping[str, Any] | None]],
        config: RunnableConfig | None = None,
    ) -> list[ToolExecutionResult]:
        results: list[ToolExecutionResult] = []
        index = 0
        while index < len(calls):
            name, arguments = calls[index]
            descriptor = self._descriptor_by_name.get(name)
            if descriptor is None or not descriptor.concurrency_safe:
                results.append(await self.execute(name, arguments, config))
                index += 1
                continue
            batch: list[tuple[str, Mapping[str, Any] | None]] = []
            while index < len(calls):
                batch_name, batch_arguments = calls[index]
                batch_descriptor = self._descriptor_by_name.get(batch_name)
                if batch_descriptor is None or not batch_descriptor.concurrency_safe:
                    break
                batch.append((batch_name, batch_arguments))
                index += 1
            results.extend(
                await asyncio.gather(
                    *(self.execute(item_name, item_args, config) for item_name, item_args in batch)
                )
            )
        return results


class ToolRuntime:
    def __init__(
        self,
        *,
        authorization: ToolAuthorizationManager = global_tool_authorizations,
        audit_log: ToolAuditLog = global_tool_audit_log,
    ) -> None:
        self.catalog = ToolCatalog()
        self.authorization = authorization
        self.audit_log = audit_log
        self.policy = ToolPolicyResolver(self.catalog, authorization)
        self.executor = ToolExecutor(authorization, audit_log)
        self._mcp_runtime: Any | None = None

    def grant_high_risk(
        self,
        *,
        session_id: str,
        tool_name: str,
        granted_by: str,
        reason: str = "",
        ttl_s: float = 300,
    ):
        descriptor = self.catalog.get(tool_name)
        if descriptor is None:
            raise ValueError(f"unknown tool: {tool_name}")
        if descriptor.risk not in {ToolRisk.HIGH, ToolRisk.CRITICAL}:
            raise ValueError(f"tool {tool_name!r} is not high-risk")
        return self.authorization.grant(
            session_id=session_id,
            tool_name=tool_name,
            granted_by=granted_by,
            reason=reason,
            ttl_s=ttl_s,
        )

    def build_toolset(self, request: ToolGrantRequest) -> ToolSet:
        snapshot = self.policy.resolve(request)
        descriptors = [
            descriptor
            for name in snapshot.granted_tools
            if (descriptor := self.catalog.get(name)) is not None
        ]
        return ToolSet(snapshot, descriptors, self.executor)

    def restore_toolset(self, snapshot: ToolGrantSnapshot) -> ToolSet:
        """Restore an exact persisted grant without broadening it through new policy."""
        missing = [name for name in snapshot.granted_tools if self.catalog.get(name) is None]
        if missing:
            raise ValueError(
                "persisted tool grant references unavailable tools: " + ", ".join(missing)
            )
        descriptors = [self.catalog.get(name) for name in snapshot.granted_tools]
        invalid_scope = [
            descriptor.name
            for descriptor in descriptors
            if descriptor is not None and snapshot.role not in descriptor.scopes
        ]
        if invalid_scope:
            raise ValueError(
                "persisted tool grant violates current role scopes: "
                + ", ".join(invalid_scope)
            )
        expired_high_risk = [
            descriptor.name
            for descriptor in descriptors
            if descriptor is not None
            and descriptor.risk == ToolRisk.HIGH
            and not self.authorization.is_granted(snapshot.session_id, descriptor.name)
        ]
        if expired_high_risk:
            raise PermissionError(
                "persisted high-risk grants expired or were revoked: "
                + ", ".join(expired_high_risk)
            )
        return ToolSet(snapshot, [item for item in descriptors if item is not None], self.executor)

    async def start_mcp(self, configs: Mapping[str, Any]) -> None:
        from core.mcp_runtime import MCPRuntime

        if self._mcp_runtime is None:
            self._mcp_runtime = MCPRuntime(self.catalog)
        await self._mcp_runtime.connect_all(configs)

    async def close(self) -> None:
        if self._mcp_runtime is not None:
            await self._mcp_runtime.close()
            self._mcp_runtime = None


global_tool_runtime = ToolRuntime()
