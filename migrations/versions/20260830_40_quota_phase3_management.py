"""Add developer-managed grants and manual quota adjustments."""

from alembic import op

from server.quota.models import QuotaAdjustmentModel, QuotaGrantModel


revision = "20260830_40_quota_phase3"
down_revision = "20260830_39_quota_phase2_closure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    QuotaGrantModel.__table__.create(bind=op.get_bind())
    QuotaAdjustmentModel.__table__.create(bind=op.get_bind())


def downgrade() -> None:
    QuotaAdjustmentModel.__table__.drop(bind=op.get_bind())
    QuotaGrantModel.__table__.drop(bind=op.get_bind())
