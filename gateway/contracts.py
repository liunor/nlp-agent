"""Versioned framework-neutral contracts for the Backend Gateway Core."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.learning import ExerciseState, LearningContext, LearningProgress
from pydantic import BaseModel, validator, Field
from typing import Optional

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TurnStatus(str, Enum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class GatewayEventType(str, Enum):
    TURN_ACCEPTED = "turn.accepted"
    TURN_STARTED = "turn.started"
    TURN_COMPLETED = "turn.completed"
    TURN_FAILED = "turn.failed"
    TURN_CANCELLED = "turn.cancelled"
    MESSAGE_DELTA = "message.delta"
    MESSAGE_COMPLETED = "message.completed"
    MESSAGE_INJECTED = "message.injected"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    WORKER_UPDATE = "worker.update"
    GAP = "stream.gap"


class EvaluationContext(BaseModel):
    """Identifies a case within one evaluation batch for observability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1, max_length=128)
    suite_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=256)

    def trace_attributes(self) -> dict[str, str]:
        return {
            "evaluation_run_id": self.run_id,
            "evaluation_suite_id": self.suite_id,
            "evaluation_case_id": self.case_id,
        }


class SubmitTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    content: str = Field(min_length=1, max_length=200_000)
    idempotency_key: str | None = Field(default=None, max_length=128)
    learning_context: LearningContext | None = None
    evaluation: EvaluationContext | None = None


class InjectMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    content: str = Field(min_length=1, max_length=200_000)


class TurnAccepted(BaseModel):
    model_config = ConfigDict(frozen=True)

    turn_id: str
    session_id: str
    status: TurnStatus
    duplicate: bool = False


class GuidedSessionRef(BaseModel):
    """Immutable guided-session evidence attached to the turn that used it."""

    model_config = ConfigDict(frozen=True)

    id: str
    blueprint_id: str | None = None
    blueprint_snapshot_sha256: str | None = None
    attempts: int = Field(default=0, ge=0)
    status: str = "active"


class TurnRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    turn_id: str
    session_id: str
    workspace_id: str
    user_id: str
    status: TurnStatus
    input_text: str
    learning_context: LearningContext | None = None
    learning_progress: LearningProgress | None = None
    guided_session: GuidedSessionRef | None = None
    exercise_state: ExerciseState | None = None
    final_text: str | None = None
    error_kind: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class GatewayEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str
    turn_id: str
    session_id: str
    sequence: int = Field(ge=1)
    type: GatewayEventType
    created_at: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)


class GatewayHealth(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    started: bool
    accepting_turns: bool
    active_turns: int
    subscribers: int
    database: str
    durable_events: int


class GatewayNotStartedError(RuntimeError):
    pass


class TurnConflictError(RuntimeError):
    pass


class ResourceNotFoundError(LookupError):
    pass


class TeachingConfigurationError(ValueError):
    """Teacher catalogue cannot safely serve the learner's current selection."""

    pass

class ChatRequest(BaseModel):
    """聊天请求体"""
    prompt: str = Field(..., description="用户输入")
    session_id: Optional[str] = None
    stream: bool = False

    #  1. 输入清洗：去除首尾空白，防止空输入绕过
    @validator('prompt')
    def sanitize_input(cls, v):
        if not v or not v.strip():
            raise ValueError("输入内容不能为空")
        # 基础清洗：移除零宽字符、控制字符等
        cleaned = ''.join(ch for ch in v if ord(ch) >= 32 or ch in '\n\r\t')
        return cleaned.strip()

class ChatResponse(BaseModel):
    """聊天响应体"""
    reply: str
    session_id: str
    # 新增安全元数据
    safety_score: Optional[float] = None