"""Stable error codes for quota-domain decisions."""

from __future__ import annotations

from enum import StrEnum


class UsageReporterConfigurationError(RuntimeError):
    """Raised when a model process cannot guarantee durable usage reporting."""


class QuotaErrorCode(StrEnum):
    POLICY_NOT_FOUND = "quota_policy_not_found"
    POLICY_AMBIGUOUS = "quota_policy_ambiguous"
    MODEL_NOT_ALLOWED = "quota_model_not_allowed"
    REQUEST_LIMIT = "quota_request_limit"
    CONCURRENCY_LIMIT = "quota_concurrency_limit"
    DAILY_EXHAUSTED = "quota_daily_exhausted"
    WEEKLY_EXHAUSTED = "quota_weekly_exhausted"
    WORKSPACE_EXHAUSTED = "quota_workspace_exhausted"
    RESERVATION_CONFLICT = "quota_reservation_conflict"
    RESERVATION_NOT_ACTIVE = "quota_reservation_not_active"
    SETTLEMENT_CONFLICT = "quota_settlement_conflict"
    INVALID_USAGE = "quota_invalid_usage"
    ADMISSION_DENIED = "admission_denied"
    UPSTREAM_PROVIDER_QUOTA_EXHAUSTED = "upstream_provider_quota_exhausted"
    OVER_LIMIT = "quota_over_limit"
    POLICY_VERSION_CONFLICT = "quota_policy_version_conflict"
    POLICY_CONFLICT = "quota_policy_conflict"
    GRANT_CONFLICT = "quota_grant_conflict"
    ADJUSTMENT_CONFLICT = "quota_adjustment_conflict"
    BINDING_CONFLICT = "quota_binding_conflict"
    INVALID_GRANT = "quota_invalid_grant"
    PRICING_RULE_CONFLICT = "quota_pricing_rule_conflict"


class QuotaDomainError(RuntimeError):
    """A deterministic domain rejection safe to expose as a machine code."""

    def __init__(self, code: QuotaErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class QuotaRejectedError(QuotaDomainError):
    """A quota admission rejection carrying the transport-ready problem DTO."""

    def __init__(self, problem) -> None:
        self.problem = problem
        super().__init__(problem.code, problem.reason)
