"""Add Phase 4 reconciliation, rollup, alert, credit, and archive state."""

import sqlalchemy as sa
from alembic import context, op
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
    offline = context.is_offline_mode()
    inspector = None if offline else sa.inspect(bind)
    if bind.dialect.name == "mysql":
        op.alter_column(
            "nlp_quota_ledger_entries",
            "entry_type",
            existing_type=sa.String(length=16),
            type_=sa.String(length=32),
            existing_nullable=False,
        )
    existing_columns = (
        set()
        if inspector is None
        else {
            column["name"]
            for column in inspector.get_columns("nlp_usage_events")
        }
    )
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
    existing_indexes = (
        set()
        if inspector is None
        else {index["name"] for index in inspector.get_indexes("nlp_usage_events")}
    )
    if "ix_nlp_usage_events_archive_occurred" not in existing_indexes:
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
        model.__table__.create(bind=bind, checkfirst=not offline)


def downgrade() -> None:
    bind = op.get_bind()
    offline = context.is_offline_mode()
    for model in (
        QuotaAlertModel,
        QuotaUsageArchiveBatchModel,
        QuotaProviderBillingModel,
        QuotaDailyRollupModel,
        QuotaCreditOperationModel,
    ):
        model.__table__.drop(bind=bind, checkfirst=not offline)
    inspector = None if offline else sa.inspect(bind)
    existing_indexes = (
        {"ix_nlp_usage_events_archive_occurred"}
        if inspector is None
        else {index["name"] for index in inspector.get_indexes("nlp_usage_events")}
    )
    if "ix_nlp_usage_events_archive_occurred" in existing_indexes:
        op.drop_index(
            "ix_nlp_usage_events_archive_occurred", table_name="nlp_usage_events"
        )
    existing_columns = (
        {"archive_batch_id", "archived_at"}
        if inspector is None
        else {
            column["name"]
            for column in inspector.get_columns("nlp_usage_events")
        }
    )
    if "archive_batch_id" in existing_columns:
        op.drop_column("nlp_usage_events", "archive_batch_id")
    if "archived_at" in existing_columns:
        op.drop_column("nlp_usage_events", "archived_at")
