"""Versioned public HTTP and WebSocket contracts for the WebUI adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from core.learning import LearningContext
from gateway.contracts import EvaluationContext


API_VERSION = "1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateSessionBody(StrictModel):
    workspace_id: str = Field(default="default", min_length=1, max_length=128)


class LoginBody(StrictModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class ReplaceUserRolesBody(StrictModel):
    role_codes: set[str] = Field(min_length=1, max_length=16)


class ReplaceRolePermissionsBody(StrictModel):
    permission_codes: set[str] = Field(max_length=128)
    scopes: dict[str, set[Literal["public", "own", "classroom", "workspace", "system"]]] = Field(default_factory=dict)


class ReplaceRoleMenusBody(StrictModel):
    menu_ids: set[str] = Field(max_length=256)


class CreateRoleBody(StrictModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=500)


class UpdateRoleStatusBody(StrictModel):
    status: Literal["active", "disabled"]


class CreateClassroomBody(StrictModel):
    workspace_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)


class ReplaceClassroomMemberBody(StrictModel):
    member_role: Literal["student", "teacher"]
    status: Literal["active", "disabled"] = "active"


class SubmitChatBody(StrictModel):
    session_id: str
    content: str = Field(min_length=1, max_length=200_000)
    idempotency_key: str | None = Field(default=None, max_length=128)
    learning_context: LearningContext | None = None
    evaluation: EvaluationContext | None = None
    model_profile: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$"
    )


class InjectChatBody(StrictModel):
    session_id: str
    content: str = Field(min_length=1, max_length=200_000)


class ToolApprovalBody(StrictModel):
    session_id: str
    tool_name: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1_000)
    ttl_s: float = Field(default=300, gt=0, le=3_600)


class UpdateSettingsBody(StrictModel):
    locale: str | None = Field(default=None, min_length=2, max_length=20)
    theme: Literal["system", "light", "dark"] | None = None
    show_reasoning: bool | None = None
    stream_render_interval_ms: int | None = Field(default=None, ge=0, le=1_000)
    default_workspace_id: str | None = Field(default=None, min_length=1, max_length=128)
    model_profile: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$"
    )


class UpdateToolPoliciesBody(StrictModel):
    policies: dict[str, Any]


class UpdateCustomToolsBody(StrictModel):
    custom: dict[str, Any]


class McpServerBody(StrictModel):
    config: dict[str, Any]


class SkillBody(StrictModel):
    content: str = Field(min_length=1, max_length=200_000)


class WorkerProfileBody(StrictModel):
    profile: dict[str, Any]


class ReleaseNoteBody(StrictModel):
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$", max_length=32)
    released_at: datetime
    notes: list[Annotated[str, StringConstraints(min_length=1, max_length=2_000)]] = Field(
        min_length=1, max_length=200
    )
    status: Literal["draft", "published"] = "published"


class CommandEnvelope(StrictModel):
    v: Literal["1"] = API_VERSION
    type: str = Field(min_length=1, max_length=100)
    request_id: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)


class ChatSendPayload(StrictModel):
    session_id: str
    content: str = Field(min_length=1, max_length=200_000)
    idempotency_key: str | None = Field(default=None, max_length=128)
    learning_context: LearningContext | None = None
    model_profile: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$"
    )


class ChatInjectPayload(StrictModel):
    session_id: str
    content: str = Field(min_length=1, max_length=200_000)


class ChatCancelPayload(StrictModel):
    turn_id: str


class SessionSubscriptionPayload(StrictModel):
    session_id: str


class StreamResumePayload(StrictModel):
    turn_id: str
    after_sequence: int = Field(default=0, ge=0)


class PingPayload(StrictModel):
    nonce: str | None = Field(default=None, max_length=128)


class ServerEventEnvelope(StrictModel):
    v: Literal["1"] = API_VERSION
    type: str
    request_id: str | None = None
    event_id: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    sequence: int | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)


WS_PAYLOAD_MODELS: dict[str, type[StrictModel]] = {
    "chat.send": ChatSendPayload,
    "chat.inject": ChatInjectPayload,
    "chat.cancel": ChatCancelPayload,
    "session.subscribe": SessionSubscriptionPayload,
    "session.unsubscribe": SessionSubscriptionPayload,
    "stream.resume": StreamResumePayload,
    "ping": PingPayload,
}


def parse_command_payload(command: CommandEnvelope) -> StrictModel:
    model = WS_PAYLOAD_MODELS.get(command.type)
    if model is None:
        raise ValueError(f"unsupported command type: {command.type}")
    return model.model_validate(command.payload)
