"""Pure Phase 0 quota contracts and balance calculations."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

from core.model_runtime.usage import (
    CanonicalTokenUsage,
    InvocationOutcome,
    ModelInvocation,
)
from server.quota.errors import QuotaErrorCode


StrictNonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
StrictPositiveInt = Annotated[int, Field(strict=True, ge=1)]

PolicySubjectType = Literal["default", "role", "user", "workspace", "classroom"]
GrantOwnerType = Literal["user", "workspace", "classroom"]
GrantSourceType = Literal["role", "purchase", "grant", "adjustment", "reset"]
GrantStatus = Literal["active", "exhausted", "expired", "revoked"]
UsageSource = Literal["provider", "estimated", "none"]
UsageStatus = Literal["exact", "estimated", "pending", "unavailable"]
ReservationStatus = Literal[
    "reserved", "running", "settling", "settled", "released", "expired"
]
ModelRole = Literal["coordinator", "worker", "utility"]
UsageWindow = Literal["day", "week"]


class QuotaFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _require_utc(value: datetime | None, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field_name} must use UTC")
    return value


class QuotaPolicy(QuotaFrozenModel):
    policy_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    version: str = Field(min_length=1)
    request_limit_micro: StrictNonNegativeInt | None = None
    daily_limit_micro: StrictNonNegativeInt | None = None
    weekly_limit_micro: StrictNonNegativeInt | None = None
    concurrency_limit: StrictNonNegativeInt | None = None
    max_overdraft_micro: StrictNonNegativeInt = 0
    allowed_model_profiles: tuple[str, ...] = ()
    unlimited: StrictBool = False


class PolicyBinding(QuotaFrozenModel):
    subject_type: PolicySubjectType
    subject_id: str = Field(min_length=1)
    policy: QuotaPolicy
    priority: StrictNonNegativeInt = 0
    effective_from: datetime
    effective_until: datetime | None = None

    @field_validator("effective_from", "effective_until")
    @classmethod
    def require_utc(cls, value: datetime | None, info):
        return _require_utc(value, field_name=info.field_name)

    @model_validator(mode="after")
    def validate_effective_range(self) -> "PolicyBinding":
        if self.effective_until is not None and self.effective_until <= self.effective_from:
            raise ValueError("effective_until must be after effective_from")
        return self

    def is_effective_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (
            self.effective_until is None or at < self.effective_until
        )


class QuotaGrant(QuotaFrozenModel):
    grant_id: str = Field(min_length=1)
    owner_type: GrantOwnerType
    owner_id: str = Field(min_length=1)
    source_type: GrantSourceType
    source_id: str | None = None
    allocated_micro: StrictNonNegativeInt
    consumed_micro: StrictNonNegativeInt = 0
    reserved_micro: StrictNonNegativeInt = 0
    effective_from: datetime
    expires_at: datetime | None = None
    status: GrantStatus = "active"
    created_by: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)

    @field_validator("effective_from", "expires_at")
    @classmethod
    def require_utc(cls, value: datetime | None, info):
        return _require_utc(value, field_name=info.field_name)

    @model_validator(mode="after")
    def validate_amounts_and_range(self) -> "QuotaGrant":
        if self.consumed_micro + self.reserved_micro > self.allocated_micro:
            raise ValueError("consumed_micro + reserved_micro must not exceed allocated_micro")
        if self.expires_at is not None and self.expires_at <= self.effective_from:
            raise ValueError("expires_at must be after effective_from")
        return self

    def is_effective_at(self, at: datetime | None = None) -> bool:
        if self.status != "active":
            return False
        if at is None:
            return True
        return self.effective_from <= at and (
            self.expires_at is None or at < self.expires_at
        )


class QuotaBalance(QuotaFrozenModel):
    allocated_micro: StrictNonNegativeInt
    adjustment_micro: StrictInt
    consumed_micro: StrictNonNegativeInt
    reserved_micro: StrictNonNegativeInt
    available_micro: StrictInt

    @model_validator(mode="after")
    def validate_available(self) -> "QuotaBalance":
        expected = (
            self.allocated_micro
            + self.adjustment_micro
            - self.consumed_micro
            - self.reserved_micro
        )
        if self.available_micro != expected:
            raise ValueError("available_micro must equal allocated + adjustment - consumed - reserved")
        return self


class AdmitTurn(QuotaFrozenModel):
    """Stable input for the Turn Admission seam."""

    request_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    workspace_id: str | None = None
    turn_id: str = Field(min_length=1)
    model_profile: str = Field(min_length=1)
    model_role: ModelRole
    estimated_input_tokens: StrictNonNegativeInt | None = None
    estimated_output_tokens: StrictNonNegativeInt
    estimated_micro: StrictNonNegativeInt = 0
    pricing_key: str | None = None
    idempotency_key: str = Field(min_length=1)


class RecordModelUsage(QuotaFrozenModel):
    """Stable input for one Runtime Attempt usage report."""

    invocation: ModelInvocation
    usage: CanonicalTokenUsage
    outcome: InvocationOutcome


class FinishTurn(QuotaFrozenModel):
    """Stable input for closing one Turn reservation."""

    reservation_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)


class UsageSnapshotQuery(QuotaFrozenModel):
    """Stable query for a user-facing usage snapshot."""

    user_id: str = Field(min_length=1)
    workspace_id: str | None = None
    window: UsageWindow = "day"
    at: datetime

    @field_validator("at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        return _require_utc(value, field_name="at")  # type: ignore[return-value]


class QuotaProblem(QuotaFrozenModel):
    """Transport-neutral problem details for quota API adapters."""

    code: QuotaErrorCode
    reason: str = Field(min_length=1)
    remaining_micro: StrictInt
    reset_at: datetime | None = None
    allowed_model_profiles: tuple[str, ...] = ()
    retryable: StrictBool

    @field_validator("reset_at")
    @classmethod
    def require_utc_reset(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value, field_name="reset_at")


class TurnAdmissionResult(QuotaFrozenModel):
    """Result returned after an admission decision."""

    allowed: StrictBool
    reservation_id: str | None = None
    reserved_micro: StrictNonNegativeInt = 0
    policy_id: str | None = None
    policy_version: str | None = None
    duplicate: StrictBool = False
    problem: QuotaProblem | None = None


class UsageRecordResult(QuotaFrozenModel):
    """Result returned after one Attempt is priced and recorded."""

    operation_id: str = Field(min_length=1)
    usage_source: UsageSource
    credits_micro: StrictNonNegativeInt
    usage_status: UsageStatus
    over_limit: StrictBool = False
    pricing_key: str | None = None
    pricing_version: str | None = None


class TurnFinishResult(QuotaFrozenModel):
    """Result returned after a reservation is closed."""

    reservation_id: str = Field(min_length=1)
    status: ReservationStatus
    released_micro: StrictNonNegativeInt


def calculate_balance(
    grants: Iterable[QuotaGrant],
    *,
    adjustment_micro: int = 0,
    at: datetime | None = None,
) -> QuotaBalance:
    """Calculate an account snapshot without mutating any Grant."""
    if isinstance(adjustment_micro, bool) or not isinstance(adjustment_micro, int):
        raise TypeError("adjustment_micro must be a strict integer")
    active_grants = [grant for grant in grants if grant.is_effective_at(at)]
    allocated = sum(grant.allocated_micro for grant in active_grants)
    consumed = sum(grant.consumed_micro for grant in active_grants)
    reserved = sum(grant.reserved_micro for grant in active_grants)
    return QuotaBalance(
        allocated_micro=allocated,
        adjustment_micro=adjustment_micro,
        consumed_micro=consumed,
        reserved_micro=reserved,
        available_micro=allocated + adjustment_micro - consumed - reserved,
    )


class Reservation(QuotaFrozenModel):
    reservation_id: str = Field(min_length=1)
    reserved_micro: StrictNonNegativeInt
    settled_micro: StrictNonNegativeInt = 0
    status: ReservationStatus = "reserved"
    max_overdraft_micro: StrictNonNegativeInt = 0
    lease_expires_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    concurrency_units: StrictPositiveInt = 1

    @field_validator("lease_expires_at", "last_heartbeat_at")
    @classmethod
    def require_utc(cls, value: datetime | None, info):
        return _require_utc(value, field_name=info.field_name)

    @model_validator(mode="after")
    def validate_terminal_state(self) -> "Reservation":
        if self.status in {"settled", "released", "expired"} and self.reserved_micro != 0:
            raise ValueError("terminal reservation must have zero reserved_micro")
        if self.status in {"released", "expired"} and self.settled_micro != 0:
            raise ValueError("released or expired reservation must have zero settled_micro")
        return self
