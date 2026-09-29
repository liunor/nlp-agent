"""Pydantic v2 contracts shared by instrumentation, storage, and debug APIs."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SpanKind(str, Enum):
    TURN = "turn"
    COORDINATOR = "coordinator"
    WORKER = "worker"
    MODEL = "model"
    TOOL = "tool"
    MEMORY = "memory"
    COMPRESSION = "compression"
    SESSION = "session"
    GATEWAY = "gateway"


class SpanStatus(str, Enum):
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    DENIED = "denied"


class TokenUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    cache_miss_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    source: Literal["provider", "estimated", "none"] = "none"


class TraceRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    trace_id: str
    request_id: str
    session_id: str
    turn_id: str
    chain_id: str | None = None
    chain_name: str | None = None
    entrypoint: str | None = None
    workspace_id: str = "default"
    user_id: str = "default"
    channel: str = "cli"
    source: str = "user"
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    ttft_ms: int | None = Field(default=None, ge=0)
    status: SpanStatus = SpanStatus.RUNNING
    usage: TokenUsage = Field(default_factory=TokenUsage)
    error_kind: str | None = None
    error_message: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class SpanRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    session_id: str
    turn_id: str
    worker_id: str | None = None
    kind: SpanKind
    name: str
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    ttft_ms: int | None = Field(default=None, ge=0)
    status: SpanStatus = SpanStatus.RUNNING
    attempt: int = Field(default=1, ge=1)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    error_kind: str | None = None
    error_message: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class TelemetryEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str
    timestamp: datetime = Field(default_factory=utc_now)
    level: Literal["debug", "info", "warning", "error"] = "info"
    name: str
    trace_id: str | None = None
    span_id: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    worker_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class TelemetryEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["trace", "span", "event"]
    payload: TraceRecord | SpanRecord | TelemetryEvent
