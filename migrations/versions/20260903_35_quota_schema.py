"""add quota management schema and versioned pricing rules (Milestone 1).

Establishes all 10 quota tables:
- nlp_pricing_rules
- nlp_usage_events
- nlp_quota_policies
- nlp_quota_policy_bindings
- nlp_quota_buckets
- nlp_quota_concurrency_locks
- nlp_quota_reservations
- nlp_quota_ledger_entries
- nlp_quota_grants
- nlp_quota_adjustments
"""

from alembic import op

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


revision = "20260903_35_quota_schema"
down_revision = "20260828_34_auth_codes"
branch_labels = None
depends_on = None

# Creation order respecting foreign key relationships
MODELS = (
    PricingRuleModel,
    UsageEventModel,
    QuotaPolicyModel,
    PolicyBindingModel,
    QuotaBucketModel,
    QuotaConcurrencyLockModel,
    QuotaReservationModel,
    QuotaLedgerEntryModel,
    QuotaGrantModel,
    QuotaAdjustmentModel,
)


def upgrade() -> None:
    bind = op.get_bind()
    for model in MODELS:
        model.__table__.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for model in reversed(MODELS):
        model.__table__.drop(bind=bind, checkfirst=True)
