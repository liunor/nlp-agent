"""Add Phase 1 immutable usage facts and versioned pricing rules."""

from alembic import op

from server.quota.models import PricingRuleModel, UsageEventModel


revision = "20260829_37_quota_usage_phase1"
down_revision = "20260829_36_usage_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    PricingRuleModel.__table__.create(bind=op.get_bind())
    UsageEventModel.__table__.create(bind=op.get_bind())


def downgrade() -> None:
    UsageEventModel.__table__.drop(bind=op.get_bind())
    PricingRuleModel.__table__.drop(bind=op.get_bind())
