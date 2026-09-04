"""Persistence models for quota policy, enforcement, and usage facts."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import BIGINT, DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from server.infrastructure.mysql.base import Base
from server.infrastructure.mysql.table_comments import TABLE_COMMENTS


UUID = String(36)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PricingRuleModel(Base):
    """Immutable versioned pricing keyed by the Runtime pricing contract."""

    __tablename__ = "nlp_pricing_rules"
    __table_args__ = (
        UniqueConstraint(
            "pricing_key", "version", name="uq_nlp_pricing_rules_key_version"
        ),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_nlp_pricing_rules_effective_range",
        ),
        Index(
            "ix_nlp_pricing_rules_key_effective",
            "pricing_key",
            "effective_from",
            "effective_until",
        ),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    pricing_key: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    effective_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    ordinary_input_credits_micro_per_million_tokens: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False
    )
    cached_input_credits_micro_per_million_tokens: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False
    )
    cache_write_credits_micro_per_million_tokens: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False
    )
    output_credits_micro_per_million_tokens: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False
    )
    reasoning_output_credits_micro_per_million_tokens: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), nullable=True
    )
    visual_input_credits_micro_per_million_tokens: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), nullable=True
    )
    image_unit_credits_micro: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), nullable=True
    )
    search_call_credits_micro: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), nullable=True
    )
    link_page_credits_micro: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )


class UsageEventModel(Base):
    """Append-only fact row for one Runtime model Attempt."""

    __tablename__ = "nlp_usage_events"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_nlp_usage_events_operation_id"),
        Index(
            "ix_nlp_usage_events_user_occurred",
            "user_id",
            "occurred_at",
        ),
        Index(
            "ix_nlp_usage_events_workspace_occurred",
            "workspace_id",
            "occurred_at",
        ),
        Index(
            "ix_nlp_usage_events_pricing_status_occurred",
            "usage_status",
            "occurred_at",
        ),
        Index(
            "ix_nlp_usage_events_provider_model_occurred",
            "provider",
            "provider_model",
            "occurred_at",
        ),
        Index(
            "ix_nlp_usage_events_archive_occurred",
            "archived_at",
            "occurred_at",
        ),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reservation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    turn_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parent_operation_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_model: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_response_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    model_profile: Mapped[str | None] = mapped_column(String(128), nullable=True)
    preset: Mapped[str] = mapped_column(String(128), nullable=False)
    route: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pricing_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempt: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    fallback_index: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    outcome_status: Mapped[str] = mapped_column(String(16), nullable=False)
    finish_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_kind: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_tokens: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    cached_input_tokens: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False
    )
    cache_write_input_tokens: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False
    )
    output_tokens: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    reasoning_output_tokens: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False
    )
    visual_input_tokens: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, default=0
    )
    image_units: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, default=0
    )
    search_calls: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, default=0
    )
    link_pages: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, default=0
    )
    total_tokens: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    usage_source: Mapped[str] = mapped_column(String(16), nullable=False)
    usage_status: Mapped[str] = mapped_column(String(16), nullable=False)
    pricing_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    credits_micro: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), nullable=True
    )
    raw_usage_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6), nullable=True
    )
    archive_batch_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )


PricingRuleModel.__table__.comment = TABLE_COMMENTS["nlp_pricing_rules"]
UsageEventModel.__table__.comment = TABLE_COMMENTS["nlp_usage_events"]


class QuotaPolicyModel(Base):
    """Versioned policy snapshot used by the admission service."""

    __tablename__ = "nlp_quota_policies"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_nlp_quota_policies_code_version"),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_nlp_quota_policies_effective_range",
        ),
        Index(
            "ix_nlp_quota_policies_status_effective",
            "status",
            "effective_from",
            "effective_until",
        ),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    request_limit_micro: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), nullable=True
    )
    daily_limit_micro: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), nullable=True
    )
    weekly_limit_micro: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), nullable=True
    )
    concurrency_limit: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), nullable=True
    )
    max_overdraft_micro: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, default=0
    )
    allowed_model_profiles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    unlimited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    effective_from: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    effective_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )


class PolicyBindingModel(Base):
    """Binding from a default, role, workspace, or user to one policy."""

    __tablename__ = "nlp_quota_policy_bindings"
    __table_args__ = (
        Index(
            "ix_nlp_quota_policy_bindings_subject_effective",
            "subject_type",
            "subject_id",
            "status",
            "effective_from",
            "effective_until",
        ),
        Index(
            "ix_nlp_quota_policy_bindings_policy",
            "policy_id",
        ),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_id: Mapped[str] = mapped_column(
        ForeignKey("nlp_quota_policies.id"), nullable=False
    )
    priority: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    effective_from: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    effective_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )


class QuotaBucketModel(Base):
    """Materialized daily/weekly counters protected by row-level locking."""

    __tablename__ = "nlp_quota_buckets"
    __table_args__ = (
        CheckConstraint(
            "bucket_type IN ('daily', 'weekly')",
            name="ck_nlp_quota_buckets_bucket_type",
        ),
        UniqueConstraint(
            "owner_type",
            "owner_id",
            "bucket_type",
            "period_start",
            "period_end",
            name="uq_nlp_quota_buckets_owner_period",
        ),
        Index(
            "ix_nlp_quota_buckets_owner_period",
            "owner_type",
            "owner_id",
            "period_start",
            "period_end",
        ),
        Index(
            "ix_nlp_quota_buckets_policy",
            "policy_id",
            "policy_version",
        ),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_id: Mapped[str] = mapped_column(
        ForeignKey("nlp_quota_policies.id"), nullable=False
    )
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    bucket_type: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    limit_micro: Mapped[int | None] = mapped_column(BIGINT(unsigned=True), nullable=True)
    consumed_micro: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, default=0
    )
    reserved_micro: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, default=0
    )
    limit_revision: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, default=1
    )
    effective_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, default=1)
    over_limit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )


class QuotaConcurrencyLockModel(Base):
    """Per-user counter used for cross-process atomic concurrency admission."""

    __tablename__ = "nlp_quota_concurrency_locks"
    __table_args__ = (
        Index(
            "ix_nlp_quota_concurrency_locks_updated",
            "updated_at",
        ),
    )

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    active_units: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, default=0
    )
    version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, default=1
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )


class QuotaReservationModel(Base):
    """One idempotent Turn reservation and its concurrency lease."""

    __tablename__ = "nlp_quota_reservations"
    __table_args__ = (
        UniqueConstraint("turn_id", name="uq_nlp_quota_reservations_turn_id"),
        UniqueConstraint(
            "idempotency_key",
            name="uq_nlp_quota_reservations_idempotency_key",
        ),
        Index(
            "ix_nlp_quota_reservations_user_status_lease",
            "user_id",
            "status",
            "lease_expires_at",
        ),
        Index(
            "ix_nlp_quota_reservations_lease",
            "status",
            "lease_expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    turn_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    policy_id: Mapped[str] = mapped_column(
        ForeignKey("nlp_quota_policies.id"), nullable=False
    )
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    reserved_micro: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, default=0
    )
    settled_micro: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, default=0
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="reserved")
    lease_expires_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    max_overdraft_micro: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, default=0
    )
    concurrency_units: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, default=1
    )
    over_limit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )


class QuotaLedgerEntryModel(Base):
    """Append-only accounting delta for reservations and settlement."""

    __tablename__ = "nlp_quota_ledger_entries"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_nlp_quota_ledger_entries_idempotency_key",
        ),
        Index(
            "ix_nlp_quota_ledger_entries_reservation_created",
            "reservation_id",
            "created_at",
        ),
        Index(
            "ix_nlp_quota_ledger_entries_bucket_created",
            "bucket_id",
            "created_at",
        ),
        Index(
            "ix_nlp_quota_ledger_entries_type_created",
            "entry_type",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    reservation_id: Mapped[str | None] = mapped_column(
        ForeignKey("nlp_quota_reservations.id"), nullable=True
    )
    bucket_id: Mapped[str | None] = mapped_column(
        ForeignKey("nlp_quota_buckets.id"), nullable=True
    )
    grant_id: Mapped[str | None] = mapped_column(UUID, nullable=True)
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_micro: Mapped[int] = mapped_column(BIGINT, nullable=False, default=0)
    reserved_delta_micro: Mapped[int] = mapped_column(BIGINT, nullable=False, default=0)
    consumed_delta_micro: Mapped[int] = mapped_column(BIGINT, nullable=False, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )


class QuotaGrantModel(Base):
    """Append-only allocation granted to a user, workspace, or classroom."""

    __tablename__ = "nlp_quota_grants"
    __table_args__ = (
        CheckConstraint(
            "bucket_type IN ('daily', 'weekly')",
            name="ck_nlp_quota_grants_bucket_type",
        ),
        UniqueConstraint(
            "owner_type",
            "owner_id",
            "idempotency_key",
            name="uq_nlp_quota_grants_owner_idempotency",
        ),
        UniqueConstraint(
            "revocation_idempotency_key",
            name="uq_nlp_quota_grants_revocation_idempotency",
        ),
        Index(
            "ix_nlp_quota_grants_owner_period_status",
            "owner_type",
            "owner_id",
            "bucket_type",
            "period_start",
            "period_end",
            "status",
        ),
        Index("ix_nlp_quota_grants_expiry", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    bucket_type: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    allocated_micro: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    revocation_idempotency_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )


class QuotaAdjustmentModel(Base):
    """Immutable manual credit/debit with operator, reason, and replay key."""

    __tablename__ = "nlp_quota_adjustments"
    __table_args__ = (
        CheckConstraint(
            "bucket_type IN ('daily', 'weekly')",
            name="ck_nlp_quota_adjustments_bucket_type",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_nlp_quota_adjustments_idempotency_key",
        ),
        Index(
            "ix_nlp_quota_adjustments_owner_period",
            "owner_type",
            "owner_id",
            "bucket_type",
            "period_start",
            "period_end",
        ),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    bucket_type: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    amount_micro: Mapped[int] = mapped_column(BIGINT, nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )


class QuotaCreditOperationModel(Base):
    """Idempotent operator intent for gifts and credit resets."""

    __tablename__ = "nlp_quota_credit_operations"
    __table_args__ = (
        CheckConstraint(
            "bucket_type IN ('daily', 'weekly')",
            name="ck_nlp_quota_credit_operations_bucket_type",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_nlp_quota_credit_operations_idempotency_key",
        ),
        Index(
            "ix_nlp_quota_credit_operations_owner_created",
            "owner_type",
            "owner_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    operation_type: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_type: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    bucket_type: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    amount_micro: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    grant_id: Mapped[str] = mapped_column(UUID, nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )


class QuotaRoleCreditOperationModel(Base):
    """Idempotent batch intent for gifting credits to a role's members."""

    __tablename__ = "nlp_quota_role_credit_operations"
    __table_args__ = (
        CheckConstraint(
            "bucket_type IN ('daily', 'weekly')",
            name="ck_nlp_quota_role_credit_operations_bucket_type",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_nlp_quota_role_credit_operations_idempotency_key",
        ),
        Index(
            "ix_nlp_quota_role_credit_operations_role_created",
            "role_code",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    role_code: Mapped[str] = mapped_column(String(64), nullable=False)
    bucket_type: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    amount_micro: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_user_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )


class QuotaCreditScopeLockModel(Base):
    """A durable row used to serialize credit changes for one owner-period."""

    __tablename__ = "nlp_quota_credit_scope_locks"

    scope_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )


class QuotaDailyRollupModel(Base):
    """Read-side daily aggregate built outside the Turn transaction."""

    __tablename__ = "nlp_quota_daily_rollups"
    __table_args__ = (
        UniqueConstraint(
            "rollup_date",
            "user_id",
            "workspace_id",
            "provider",
            "provider_model",
            "purpose",
            name="uq_nlp_quota_daily_rollups_dimension",
        ),
        Index(
            "ix_nlp_quota_daily_rollups_workspace_date",
            "workspace_id",
            "rollup_date",
        ),
        Index(
            "ix_nlp_quota_daily_rollups_user_date",
            "user_id",
            "rollup_date",
        ),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    rollup_date: Mapped[date] = mapped_column(Date, nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_model: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    event_count: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    exact_events: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    estimated_events: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    pending_events: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    unavailable_events: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    priced_credits_micro: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False
    )
    input_tokens: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    cached_input_tokens: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False
    )
    cache_write_input_tokens: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False
    )
    output_tokens: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    reasoning_output_tokens: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False
    )
    total_tokens: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )


class QuotaProviderBillingModel(Base):
    """Immutable provider billing line with a mutable reconciliation result."""

    __tablename__ = "nlp_quota_provider_billing"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "statement_id",
            name="uq_nlp_quota_provider_billing_statement",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_nlp_quota_provider_billing_idempotency_key",
        ),
        Index(
            "ix_nlp_quota_provider_billing_operation",
            "operation_id",
        ),
        Index(
            "ix_nlp_quota_provider_billing_status_date",
            "status",
            "billed_at",
        ),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    statement_id: Mapped[str] = mapped_column(String(255), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    billed_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    billed_credits_micro: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), nullable=True
    )
    billed_tokens_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    matched_usage_event_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    local_credits_micro: Mapped[int | None] = mapped_column(BIGINT, nullable=True)
    difference_micro: Mapped[int | None] = mapped_column(BIGINT, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )
    reconciled_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6), nullable=True
    )


class QuotaUsageArchiveBatchModel(Base):
    """Manifest for a non-destructive UsageEvent archive pass."""

    __tablename__ = "nlp_quota_usage_archive_batches"
    __table_args__ = (
        Index(
            "ix_nlp_quota_usage_archive_batches_cutoff",
            "cutoff_at",
        ),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    cutoff_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    event_count: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6), nullable=True
    )


class QuotaAlertModel(Base):
    """Deduplicated operational alert generated from read-side rollups."""

    __tablename__ = "nlp_quota_alerts"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_nlp_quota_alerts_dedupe_key"),
        Index(
            "ix_nlp_quota_alerts_status_created",
            "status",
            "created_at",
        ),
        Index(
            "ix_nlp_quota_alerts_owner_window",
            "owner_type",
            "owner_id",
            "window_start",
        ),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_type: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    baseline_micro: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    actual_micro: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    threshold_multiplier: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, default=_utc_now
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6), nullable=True
    )


QuotaPolicyModel.__table__.comment = TABLE_COMMENTS["nlp_quota_policies"]
PolicyBindingModel.__table__.comment = TABLE_COMMENTS["nlp_quota_policy_bindings"]
QuotaBucketModel.__table__.comment = TABLE_COMMENTS["nlp_quota_buckets"]
QuotaConcurrencyLockModel.__table__.comment = TABLE_COMMENTS["nlp_quota_concurrency_locks"]
QuotaReservationModel.__table__.comment = TABLE_COMMENTS["nlp_quota_reservations"]
QuotaLedgerEntryModel.__table__.comment = TABLE_COMMENTS["nlp_quota_ledger_entries"]
QuotaGrantModel.__table__.comment = TABLE_COMMENTS["nlp_quota_grants"]
QuotaAdjustmentModel.__table__.comment = TABLE_COMMENTS["nlp_quota_adjustments"]
QuotaCreditOperationModel.__table__.comment = TABLE_COMMENTS["nlp_quota_credit_operations"]
QuotaRoleCreditOperationModel.__table__.comment = TABLE_COMMENTS["nlp_quota_role_credit_operations"]
QuotaCreditScopeLockModel.__table__.comment = TABLE_COMMENTS["nlp_quota_credit_scope_locks"]
QuotaDailyRollupModel.__table__.comment = TABLE_COMMENTS["nlp_quota_daily_rollups"]
QuotaProviderBillingModel.__table__.comment = TABLE_COMMENTS["nlp_quota_provider_billing"]
QuotaUsageArchiveBatchModel.__table__.comment = TABLE_COMMENTS["nlp_quota_usage_archive_batches"]
QuotaAlertModel.__table__.comment = TABLE_COMMENTS["nlp_quota_alerts"]
