"""Add Phase 2 quota policies, buckets, reservations, and ledger."""

from alembic import op

from server.quota.models import (
    PolicyBindingModel,
    QuotaBucketModel,
    QuotaLedgerEntryModel,
    QuotaPolicyModel,
    QuotaReservationModel,
)


revision = "20260829_38"
down_revision = "20260829_37_quota_usage_phase1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    QuotaPolicyModel.__table__.create(bind=op.get_bind())
    PolicyBindingModel.__table__.create(bind=op.get_bind())
    QuotaBucketModel.__table__.create(bind=op.get_bind())
    QuotaReservationModel.__table__.create(bind=op.get_bind())
    QuotaLedgerEntryModel.__table__.create(bind=op.get_bind())


def downgrade() -> None:
    QuotaLedgerEntryModel.__table__.drop(bind=op.get_bind())
    QuotaReservationModel.__table__.drop(bind=op.get_bind())
    QuotaBucketModel.__table__.drop(bind=op.get_bind())
    PolicyBindingModel.__table__.drop(bind=op.get_bind())
    QuotaPolicyModel.__table__.drop(bind=op.get_bind())
