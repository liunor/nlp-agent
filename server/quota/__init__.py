"""Quota domain, usage facts, and enforcement modules.

The package records immutable Runtime Attempts, computes Shadow Credits, and
provides the transactional admission/settlement seam used by Gateway and
Worker processes.
"""

from __future__ import annotations

from server.quota.contracts import (
    AdmitTurn,
    FinishTurn,
    PolicyBinding,
    QuotaBalance,
    QuotaGrant,
    QuotaPolicy,
    QuotaProblem,
    RecordModelUsage,
    Reservation,
    TurnAdmissionResult,
    TurnFinishResult,
    UsageRecordResult,
    UsageSnapshotQuery,
    calculate_balance,
)
from server.quota.errors import (
    QuotaDomainError,
    QuotaErrorCode,
    QuotaRejectedError,
    UsageReporterConfigurationError,
)
from server.quota.models import (
    PolicyBindingModel,
    PricingRuleModel,
    QuotaAdjustmentModel,
    QuotaBucketModel,
    QuotaConcurrencyLockModel,
    QuotaGrantModel,
    QuotaLedgerEntryModel,
    QuotaPolicyModel,
    QuotaReservationModel,
    UsageEventModel,
)
from server.quota.bootstrap import (
    configure_usage_reporter,
    shutdown_usage_reporter,
)
from server.quota.policy import resolve_effective_policy
from server.quota.pricing import (
    EstimatedUsageCannotBePricedError,
    PricedUsage,
    PricingCatalog,
    PricingError,
    PricingRule,
    UnknownPricingKeyError,
    UnknownUsageCannotBePricedError,
)
from server.quota.reporting import (
    DurableModelUsageReporter,
    UsageEventConflictError,
)
from server.quota.reservation import begin, expire, release, renew, settle
from server.quota.service import QuotaService

__all__ = [
    "AdmitTurn",
    "DurableModelUsageReporter",
    "EstimatedUsageCannotBePricedError",
    "FinishTurn",
    "PolicyBinding",
    "PolicyBindingModel",
    "PricedUsage",
    "PricingCatalog",
    "PricingError",
    "PricingRule",
    "PricingRuleModel",
    "QuotaAdjustmentModel",
    "QuotaBalance",
    "QuotaBucketModel",
    "QuotaConcurrencyLockModel",
    "QuotaDomainError",
    "QuotaErrorCode",
    "QuotaGrant",
    "QuotaGrantModel",
    "QuotaLedgerEntryModel",
    "QuotaPolicy",
    "QuotaPolicyModel",
    "QuotaProblem",
    "QuotaRejectedError",
    "QuotaReservationModel",
    "QuotaService",
    "RecordModelUsage",
    "Reservation",
    "TurnAdmissionResult",
    "TurnFinishResult",
    "UnknownPricingKeyError",
    "UnknownUsageCannotBePricedError",
    "UsageEventConflictError",
    "UsageEventModel",
    "UsageRecordResult",
    "UsageReporterConfigurationError",
    "UsageSnapshotQuery",
    "begin",
    "calculate_balance",
    "configure_usage_reporter",
    "expire",
    "release",
    "renew",
    "resolve_effective_policy",
    "settle",
    "shutdown_usage_reporter",
]
