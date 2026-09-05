"""Versioned public HTTP and WebSocket contracts for the WebUI adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, field_validator, model_validator
from core.learning import LearningContext
from gateway.contracts import EvaluationContext


API_VERSION = "1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateSessionBody(StrictModel):
    workspace_id: str = Field(default="default", min_length=1, max_length=128)


class RenameSessionBody(StrictModel):
    title: str = Field(min_length=1, max_length=255)


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
    # An empty selection is intentional: the service converts it to the
    # least-privilege guest role instead of leaving an account roleless.
    role_codes: set[str] = Field(max_length=4)


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


class ModelConfigBody(StrictModel):
    config: dict[str, Any]


class ReleaseNoteBody(StrictModel):
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$", max_length=32)
    released_at: datetime
    notes: list[Annotated[str, StringConstraints(min_length=1, max_length=2_000)]] = Field(
        min_length=1, max_length=200
    )
    status: Literal["draft", "published"] = "published"


class QuotaPolicyBody(StrictModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")
    version: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    request_limit_micro: StrictInt | None = Field(default=None, ge=0)
    daily_limit_micro: StrictInt | None = Field(default=None, ge=0)
    weekly_limit_micro: StrictInt | None = Field(default=None, ge=0)
    concurrency_limit: StrictInt | None = Field(default=None, ge=0)
    max_overdraft_micro: StrictInt = Field(default=0, ge=0)
    allowed_model_profiles: list[str] = Field(default_factory=list, max_length=128)
    unlimited: bool = False
    effective_from: datetime
    effective_until: datetime | None = None
    # Publishing is a separate audited action.  Accepting ``active`` here
    # would let a caller bypass the publish workflow and its validation.
    status: Literal["draft"] = "draft"


class QuotaPolicyUpdateBody(StrictModel):
    code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]{0,127}$")
    version: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    request_limit_micro: StrictInt | None = Field(default=None, ge=0)
    daily_limit_micro: StrictInt | None = Field(default=None, ge=0)
    weekly_limit_micro: StrictInt | None = Field(default=None, ge=0)
    concurrency_limit: StrictInt | None = Field(default=None, ge=0)
    max_overdraft_micro: StrictInt | None = Field(default=None, ge=0)
    allowed_model_profiles: list[str] | None = Field(default=None, max_length=128)
    unlimited: bool | None = None
    effective_from: datetime | None = None
    effective_until: datetime | None = None


class QuotaBindingBody(StrictModel):
    subject_type: Literal["default", "role", "user", "workspace", "classroom"]
    subject_id: str = Field(min_length=1, max_length=128)
    policy_id: str = Field(min_length=1, max_length=36)
    priority: StrictInt = Field(default=0, ge=0)
    effective_from: datetime
    effective_until: datetime | None = None


class QuotaGrantBody(StrictModel):
    owner_type: Literal["user", "workspace", "classroom"]
    owner_id: str = Field(min_length=1, max_length=128)
    bucket_type: Literal["daily", "weekly"]
    period_start: datetime
    period_end: datetime
    allocated_micro: StrictInt = Field(ge=0)
    source_type: Literal["role", "purchase", "grant", "adjustment", "reset"]
    source_id: str | None = Field(default=None, max_length=128)
    reason: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=1, max_length=255)
    effective_from: datetime
    expires_at: datetime | None = None


class QuotaAdjustmentBody(StrictModel):
    owner_type: Literal["user", "workspace", "classroom"]
    owner_id: str = Field(min_length=1, max_length=128)
    bucket_type: Literal["daily", "weekly"]
    period_start: datetime
    period_end: datetime
    amount_micro: StrictInt
    reason: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=1, max_length=255)


class QuotaGrantRevokeBody(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=255)


class QuotaPricingRuleBody(StrictModel):
    pricing_key: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=64)
    effective_from: datetime
    effective_until: datetime | None = None
    ordinary_input_credits_micro_per_million_tokens: StrictInt = Field(ge=0)
    cached_input_credits_micro_per_million_tokens: StrictInt = Field(ge=0)
    cache_write_credits_micro_per_million_tokens: StrictInt = Field(ge=0)
    output_credits_micro_per_million_tokens: StrictInt = Field(ge=0)
    reasoning_output_credits_micro_per_million_tokens: StrictInt | None = Field(
        default=None, ge=0
    )
    visual_input_credits_micro_per_million_tokens: StrictInt | None = Field(
        default=None, ge=0
    )
    image_unit_credits_micro: StrictInt | None = Field(default=None, ge=0)
    search_call_credits_micro: StrictInt | None = Field(default=None, ge=0)
    link_page_credits_micro: StrictInt | None = Field(default=None, ge=0)


class QuotaBillingStatementBody(StrictModel):
    provider: str = Field(min_length=1, max_length=128)
    statement_id: str = Field(min_length=1, max_length=255)
    operation_id: str = Field(min_length=1, max_length=128)
    billed_at: datetime
    billed_credits_micro: StrictInt | None = Field(default=None, ge=0)
    billed_tokens: dict[str, StrictInt] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=255)


class QuotaBillingReconcileBody(StrictModel):
    statements: list[QuotaBillingStatementBody] = Field(min_length=1, max_length=10_000)


class QuotaCreditOperationBody(StrictModel):
    owner_type: Literal["user", "workspace", "classroom"]
    owner_id: str = Field(min_length=1, max_length=128)
    bucket_type: Literal["daily", "weekly"]
    period_start: datetime
    period_end: datetime
    amount_micro: StrictInt = Field(ge=0)
    reason: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=1, max_length=255)
    effective_from: datetime
    expires_at: datetime | None = None


class QuotaRoleCreditOperationBody(StrictModel):
    role_code: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    bucket_type: Literal["daily", "weekly"]
    period_start: datetime
    period_end: datetime
    amount_micro: StrictInt = Field(ge=0)
    reason: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=1, max_length=255)
    effective_from: datetime
    expires_at: datetime | None = None


class QuotaBillingRepairBody(StrictModel):
    reason: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=1, max_length=255)


class QuotaUsageArchiveBody(StrictModel):
    before: datetime
    batch_size: StrictInt = Field(default=10_000, ge=1, le=100_000)


class QuotaAlertStatusBody(StrictModel):
    status: Literal["acknowledged", "resolved"]
    reason: str = Field(min_length=1, max_length=255)


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
