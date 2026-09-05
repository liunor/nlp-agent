"""Add Phase 4 reconciliation, rollup, alert, credit, and archive state."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

from server.quota.models import (
    QuotaAlertModel,
    QuotaCreditOperationModel,
    QuotaDailyRollupModel,
    QuotaProviderBillingModel,
    QuotaUsageArchiveBatchModel,
)


revision = "20260830_42_quota_phase4"
down_revision = "20260830_41_quota_menu"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if bind.dialect.name == "mysql":
        op.alter_column(
            "nlp_quota_ledger_entries",
            "entry_type",
            existing_type=sa.String(length=16),
            type_=sa.String(length=32),
            existing_nullable=False,
        )
    existing_columns = {
        column["name"] for column in inspector.get_columns("nlp_usage_events")
    }
    if "archived_at" not in existing_columns:
        op.add_column(
            "nlp_usage_events",
            sa.Column("archived_at", DATETIME(fsp=6), nullable=True),
        )
    if "archive_batch_id" not in existing_columns:
        op.add_column(
            "nlp_usage_events",
            sa.Column("archive_batch_id", sa.String(length=36), nullable=True),
        )
    if "ix_nlp_usage_events_archive_occurred" not in {
        index["name"] for index in inspector.get_indexes("nlp_usage_events")
    }:
        op.create_index(
            "ix_nlp_usage_events_archive_occurred",
            "nlp_usage_events",
            ["archived_at", "occurred_at"],
        )
    for model in (
        QuotaCreditOperationModel,
        QuotaDailyRollupModel,
        QuotaProviderBillingModel,
        QuotaUsageArchiveBatchModel,
        QuotaAlertModel,
    ):
        model.__table__.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for model in (
        QuotaAlertModel,
        QuotaUsageArchiveBatchModel,
        QuotaProviderBillingModel,
        QuotaDailyRollupModel,
        QuotaCreditOperationModel,
    ):
        model.__table__.drop(bind=bind, checkfirst=True)
    inspector = sa.inspect(bind)
    if "ix_nlp_usage_events_archive_occurred" in {
        index["name"] for index in inspector.get_indexes("nlp_usage_events")
    }:
        op.drop_index(
            "ix_nlp_usage_events_archive_occurred", table_name="nlp_usage_events"
        )
    existing_columns = {
        column["name"] for column in inspector.get_columns("nlp_usage_events")
    }
    if "archive_batch_id" in existing_columns:
        op.drop_column("nlp_usage_events", "archive_batch_id")
    if "archived_at" in existing_columns:
        op.drop_column("nlp_usage_events", "archived_at")
