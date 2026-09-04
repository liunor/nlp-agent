"""Stable usage contracts, models, and attribution context management."""

from __future__ import annotations

import contextvars
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.observability.context import current_telemetry_context


UsageSource = Literal["provider", "estimated", "none"]
UsageSemantics = Literal["final", "cumulative", "delta", "partial"]
UsagePurpose = Literal[
    "coordinator",
    "worker",
    "compact",
    "memory",
    "vision",
    "evaluation",
    "other",
]
InvocationStatus = Literal["succeeded", "failed", "cancelled", "interrupted"]
StrictNonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
StrictPositiveInt = Annotated[int, Field(strict=True, ge=1)]


class MissingUsageAttributionError(RuntimeError):
    """Raised when an LLM invocation cannot resolve mandatory usage attribution."""


class UsageReporterUnavailableError(RuntimeError):
    """Raised when a required model-process usage Reporter is not configured."""


class UsageFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ModelIdentity(UsageFrozenModel):
    provider: str = Field(min_length=1)
    provider_model: str = Field(min_length=1)
    model_profile: str | None = None
    preset: str = Field(min_length=1)
    route: str | None = None
    pricing_key: str | None = None
    context_window_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)


class UsageAttributionContext(UsageFrozenModel):
    request_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    workspace_id: str | None = None
    conversation_id: str | None = None
    turn_id: str | None = None
    reservation_id: str | None = None
    worker_id: str | None = None
    parent_operation_id: str | None = None
    purpose: UsagePurpose


class CanonicalTokenUsage(UsageFrozenModel):
    input_tokens: StrictNonNegativeInt = 0
    cached_input_tokens: StrictNonNegativeInt = 0
    cache_write_input_tokens: StrictNonNegativeInt = 0
    output_tokens: StrictNonNegativeInt = 0
    reasoning_output_tokens: StrictNonNegativeInt = 0
    total_tokens: StrictNonNegativeInt = 0
    source: UsageSource = "none"
    semantics: UsageSemantics = "final"
    provider_response_id: str | None = None

    @model_validator(mode="after")
    def validate_subsets_and_total(self) -> "CanonicalTokenUsage":
        if self.cached_input_tokens + self.cache_write_input_tokens > self.input_tokens:
            raise ValueError(
                "cached_input_tokens + cache_write_input_tokens "
                "must not exceed input_tokens"
            )
        if self.reasoning_output_tokens > self.output_tokens:
            raise ValueError("reasoning_output_tokens must be a subset of output_tokens")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens + output_tokens")
        if self.source == "none" and any((
            self.input_tokens,
            self.cached_input_tokens,
            self.cache_write_input_tokens,
            self.output_tokens,
            self.reasoning_output_tokens,
            self.total_tokens,
        )):
            raise ValueError("source=none cannot carry token values")
        return self


class BillableFeatureUsage(UsageFrozenModel):
    """Non-text usage facts carried beside, never inside, canonical Token usage."""

    visual_input_tokens: StrictNonNegativeInt = 0
    image_units: StrictNonNegativeInt = 0
    search_calls: StrictNonNegativeInt = 0
    link_pages: StrictNonNegativeInt = 0

    @model_validator(mode="after")
    def validate_vision_fallback(self) -> "BillableFeatureUsage":
        if self.visual_input_tokens and self.image_units:
            raise ValueError(
                "visual_input_tokens and image_units are mutually exclusive"
            )
        return self


class ModelInvocation(UsageFrozenModel):
    operation_id: str = Field(min_length=1)
    identity: ModelIdentity
    attribution: UsageAttributionContext
    attempt: StrictPositiveInt
    fallback_index: StrictNonNegativeInt
    started_at: datetime
    feature_usage: BillableFeatureUsage = Field(
        default_factory=BillableFeatureUsage
    )

    @field_validator("operation_id")
    @classmethod
    def validate_operation_id(cls, value: str) -> str:
        try:
            parsed = UUID(value)
        except ValueError as err:
            raise ValueError("operation_id must be a UUIDv4") from err
        if parsed.version != 4:
            raise ValueError("operation_id must be a UUIDv4")
        return value

    @field_validator("started_at")
    @classmethod
    def validate_started_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("started_at must be timezone-aware")
        if value.utcoffset().total_seconds() != 0:
            raise ValueError("started_at must use UTC")
        return value


class InvocationOutcome(UsageFrozenModel):
    status: InvocationStatus
    finish_reason: str | None = None
    error_kind: str | None = None
    completed_at: datetime

    @field_validator("completed_at")
    @classmethod
    def validate_completed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("completed_at must be timezone-aware")
        if value.utcoffset().total_seconds() != 0:
            raise ValueError("completed_at must use UTC")
        return value


class ModelUsageReporter(Protocol):
    async def report(
        self,
        invocation: ModelInvocation,
        usage: CanonicalTokenUsage,
        outcome: InvocationOutcome,
    ) -> None:
        """Persist one Provider invocation idempotently by operation_id."""
        ...


_CURRENT_ATTRIBUTION: contextvars.ContextVar[UsageAttributionContext | None] = (
    contextvars.ContextVar("nlp_usage_attribution", default=None)
)
_CURRENT_PURPOSE: contextvars.ContextVar[UsagePurpose | None] = (
    contextvars.ContextVar("nlp_usage_purpose", default=None)
)
_CURRENT_FEATURE_USAGE: contextvars.ContextVar[BillableFeatureUsage | None] = (
    contextvars.ContextVar("nlp_billable_feature_usage", default=None)
)


def current_usage_attribution() -> UsageAttributionContext | None:
    return _CURRENT_ATTRIBUTION.get()


def current_billable_feature_usage() -> BillableFeatureUsage:
    return _CURRENT_FEATURE_USAGE.get() or BillableFeatureUsage()


@contextmanager
def bind_usage_attribution(
    context: UsageAttributionContext,
) -> Iterator[UsageAttributionContext]:
    token = _CURRENT_ATTRIBUTION.set(context)
    try:
        yield context
    finally:
        _CURRENT_ATTRIBUTION.reset(token)


@contextmanager
def bind_usage_purpose(purpose: UsagePurpose) -> Iterator[None]:
    token_purpose = _CURRENT_PURPOSE.set(purpose)
    curr = _CURRENT_ATTRIBUTION.get()
    token_attr = None
    if curr is not None:
        token_attr = _CURRENT_ATTRIBUTION.set(curr.model_copy(update={"purpose": purpose}))
    try:
        yield
    finally:
        if token_attr is not None:
            _CURRENT_ATTRIBUTION.reset(token_attr)
        _CURRENT_PURPOSE.reset(token_purpose)


@contextmanager
def bind_billable_feature_usage(
    usage: BillableFeatureUsage,
) -> Iterator[BillableFeatureUsage]:
    token = _CURRENT_FEATURE_USAGE.set(usage)
    try:
        yield usage
    finally:
        _CURRENT_FEATURE_USAGE.reset(token)


def resolve_usage_attribution() -> UsageAttributionContext:
    attr = _CURRENT_ATTRIBUTION.get()
    if attr is not None:
        return attr
    override_purpose = _CURRENT_PURPOSE.get()
    telem = current_telemetry_context()
    if telem is not None:
        purpose: UsagePurpose = override_purpose or (
            "worker" if telem.worker_id else "coordinator"
        )
        return UsageAttributionContext(
            request_id=telem.request_id,
            user_id=telem.user_id,
            workspace_id=telem.workspace_id,
            conversation_id=telem.session_id,
            turn_id=telem.turn_id,
            worker_id=telem.worker_id,
            purpose=purpose,
        )
    raise MissingUsageAttributionError(
        "No usage attribution context or telemetry context available to resolve attribution"
    )


@contextmanager
def system_usage_attribution(
    *,
    purpose: UsagePurpose,
    request_id: str | None = None,
) -> Iterator[UsageAttributionContext]:
    ctx = UsageAttributionContext(
        request_id=request_id or uuid.uuid4().hex,
        user_id="system",
        workspace_id=None,
        purpose=purpose,
    )
    token = _CURRENT_ATTRIBUTION.set(ctx)
    try:
        yield ctx
    finally:
        _CURRENT_ATTRIBUTION.reset(token)
