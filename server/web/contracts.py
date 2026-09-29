"""Versioned public HTTP and WebSocket contracts for the WebUI adapter."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from core.learning import LearningContext
from gateway.contracts import EvaluationContext


API_VERSION = "1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateSessionBody(StrictModel):
    workspace_id: str = Field(default="default", min_length=1, max_length=128)


class CreateWhiteboardLibraryBody(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    elements: list[dict[str, Any]] = Field(min_length=1, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("elements")
    @classmethod
    def validate_elements(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if any(
            not isinstance(element.get("id"), str)
            or not element["id"].strip()
            or not isinstance(element.get("type"), str)
            or not element["type"].strip()
            for element in value
        ):
            raise ValueError("素材元素必须包含有效的 id 和 type")
        if any(element.get("type") in {"image", "iframe", "embeddable"} for element in value):
            raise ValueError("素材不能包含图片或嵌入式元素")
        try:
            serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("素材元素必须是可序列化的 JSON") from error
        if len(serialized.encode("utf-8")) > 512_000:
            raise ValueError("素材不能超过 512 KB")
        return value


class RenameWhiteboardLibraryBody(StrictModel):
    name: str = Field(min_length=1, max_length=128)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class LoginBody(StrictModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)
    workspace_id: str | None = Field(default=None, min_length=1, max_length=128)


class FeedbackBody(StrictModel):
    body: str = Field(min_length=1, max_length=2_000)
    category: Literal["feature", "ux", "bug", "other"] | None = None

    @field_validator("body", mode="before")
    @classmethod
    def strip_body(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("category", mode="before")
    @classmethod
    def normalize_category(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip().lower()
            return normalized or None
        return value


class FeedbackReadBody(StrictModel):
    read_through_message_id: str = Field(min_length=1, max_length=128)


class FeedbackBulkBody(StrictModel):
    thread_ids: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]] = Field(
        min_length=1,
        max_length=200,
    )


FeedbackCategoryValue = Literal["feature", "ux", "bug", "other"]
FeedbackStatusValue = Literal["open", "under_review", "planned", "in_progress", "complete", "closed"]
FeedbackPriorityValue = Literal["low", "medium", "high"]
FeedbackSortValue = Literal["latest", "oldest", "unread"]


class FeedbackUpdateBody(StrictModel):
    status: FeedbackStatusValue | None = None
    category: FeedbackCategoryValue | None = None
    priority: FeedbackPriorityValue | None = None

    @model_validator(mode="after")
    def require_change(self) -> "FeedbackUpdateBody":
        if self.status is None and self.category is None and self.priority is None:
            raise ValueError("至少提供一个反馈字段")
        return self


class FeedbackReplyBody(StrictModel):
    body: str = Field(min_length=1, max_length=2_000)

    @field_validator("body", mode="before")
    @classmethod
    def strip_body(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


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



class ChatAttachment(StrictModel):
    file_name: str = Field(min_length=1, max_length=256)


class SubmitChatBody(StrictModel):
    session_id: str
    content: str = Field(default="", max_length=200_000)
    attachments: list[ChatAttachment] = Field(default_factory=list, max_length=5)
    idempotency_key: str | None = Field(default=None, max_length=128)
    learning_context: LearningContext | None = None
    evaluation: EvaluationContext | None = None
    model_profile: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$"
    )

    @model_validator(mode="after")
    def require_content_or_attachment(self) -> "SubmitChatBody":
        if not self.content.strip() and not self.attachments:
            raise ValueError("content 或 attachments 至少提供一项")
        return self


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
    content_font_size: Literal["small", "medium", "large"] | None = None
    reduce_motion: bool | None = None
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
    content: str = Field(default="", max_length=200_000)
    attachments: list[ChatAttachment] = Field(default_factory=list, max_length=5)
    idempotency_key: str | None = Field(default=None, max_length=128)
    learning_context: LearningContext | None = None
    model_profile: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$"
    )

    @model_validator(mode="after")
    def require_content_or_attachment(self) -> "ChatSendPayload":
        if not self.content.strip() and not self.attachments:
            raise ValueError("content 或 attachments 至少提供一项")
        return self


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
