"""Add capability usage metering tables and extend usage events and billing.

Revision ID: 20260902_45_cap_usage_metering
Revises: 20260901_44_quota_summary
Create Date: 2026-09-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import BIGINT, DATETIME


revision = "20260902_45_cap_usage_metering"
down_revision = "20260901_44_quota_summary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "nlp_usage_events",
        sa.Column("text_input_tokens", BIGINT(unsigned=True), nullable=True),
    )
    op.add_column(
        "nlp_usage_events",
        sa.Column(
            "image_input_tokens",
            BIGINT(unsigned=True),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "nlp_usage_events",
        sa.Column(
            "usage_details_json",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
    )

    op.create_table(
        "nlp_meter_pricing_rules",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("pricing_key", sa.String(255), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("meter", sa.String(128), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("rate_unit", BIGINT(unsigned=True), nullable=False),
        sa.Column("rate_micro", BIGINT(unsigned=True), nullable=False),
        sa.Column(
            "minimum_charge_micro",
            BIGINT(unsigned=True),
            nullable=False,
            server_default="0",
        ),
        sa.Column("effective_from", DATETIME(fsp=6), nullable=False),
        sa.Column("effective_until", DATETIME(fsp=6), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pricing_key", "version", "meter",
            name="uq_nlp_meter_pricing_rules_key_ver_meter",
        ),
        sa.CheckConstraint("rate_unit > 0", name="ck_nlp_meter_pricing_rules_rate_unit"),
        sa.CheckConstraint(
            "effective_until is null or effective_until > effective_from",
            name="ck_nlp_meter_pricing_rules_effective_range",
        ),
        comment="按 pricing_key 与 meter 版本化保存的通用计量项换算规则。",
    )
    op.create_index(
        "ix_nlp_meter_pricing_rules_key_eff",
        "nlp_meter_pricing_rules",
        ["pricing_key", "effective_from", "effective_until"],
    )
    op.create_index(
        "ix_nlp_meter_pricing_rules_status_eff",
        "nlp_meter_pricing_rules",
        ["status", "effective_from"],
    )

    op.create_table(
        "nlp_capability_usage_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("operation_id", sa.String(128), nullable=False),
        sa.Column("parent_operation_id", sa.String(128), nullable=True),
        sa.Column("reservation_id", sa.String(128), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=True),
        sa.Column("conversation_id", sa.String(128), nullable=True),
        sa.Column("turn_id", sa.String(128), nullable=True),
        sa.Column("worker_id", sa.String(128), nullable=True),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("capability_type", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("provider_response_id", sa.String(255), nullable=True),
        sa.Column("pricing_key", sa.String(255), nullable=False),
        sa.Column("pricing_version", sa.String(64), nullable=True),
        sa.Column("usage_source", sa.String(16), nullable=False),
        sa.Column("usage_status", sa.String(16), nullable=False),
        sa.Column("credits_micro", BIGINT(unsigned=True), nullable=True),
        sa.Column("raw_usage_json", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("occurred_at", DATETIME(fsp=6), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("archived_at", DATETIME(fsp=6), nullable=True),
        sa.Column("archive_batch_id", sa.String(36), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", name="uq_nlp_capability_usage_events_op_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_nlp_capability_usage_events_idemp_key"),
        comment="每次能力（搜索、网页抽取、OCR）执行的不可变用量事实与 Credits。",
    )
    op.create_index(
        "ix_nlp_cap_usage_user_occurred",
        "nlp_capability_usage_events",
        ["user_id", "occurred_at"],
    )
    op.create_index(
        "ix_nlp_cap_usage_workspace_occurred",
        "nlp_capability_usage_events",
        ["workspace_id", "occurred_at"],
    )
    op.create_index(
        "ix_nlp_cap_usage_type_occurred",
        "nlp_capability_usage_events",
        ["capability_type", "occurred_at"],
    )
    op.create_index(
        "ix_nlp_cap_usage_provider_occurred",
        "nlp_capability_usage_events",
        ["provider", "occurred_at"],
    )
    op.create_index(
        "ix_nlp_cap_usage_status_occurred",
        "nlp_capability_usage_events",
        ["usage_status", "occurred_at"],
    )
    op.create_index(
        "ix_nlp_cap_usage_res_occurred",
        "nlp_capability_usage_events",
        ["reservation_id", "occurred_at"],
    )

    op.create_table(
        "nlp_capability_usage_items",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("meter", sa.String(128), nullable=False),
        sa.Column("quantity", BIGINT(unsigned=True), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("rate_unit", BIGINT(unsigned=True), nullable=True),
        sa.Column("rate_micro", BIGINT(unsigned=True), nullable=True),
        sa.Column("line_credits_micro", BIGINT(unsigned=True), nullable=True),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["nlp_capability_usage_events.id"],
            name="fk_nlp_capability_usage_items_event",
        ),
        sa.UniqueConstraint("event_id", "meter", name="uq_nlp_capability_usage_items_event_meter"),
        comment="能力用量事件对应的计量明细项及计价结果。",
    )
    op.create_index(
        "ix_nlp_capability_usage_items_meter_created",
        "nlp_capability_usage_items",
        ["meter", "created_at"],
    )

    op.add_column(
        "nlp_quota_provider_billing",
        sa.Column(
            "usage_event_type",
            sa.String(16),
            nullable=False,
            server_default="model",
        ),
    )
    op.add_column(
        "nlp_quota_provider_billing",
        sa.Column("matched_capability_event_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "nlp_quota_provider_billing",
        sa.Column(
            "billed_usage_json",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("nlp_quota_provider_billing", "billed_usage_json")
    op.drop_column("nlp_quota_provider_billing", "matched_capability_event_id")
    op.drop_column("nlp_quota_provider_billing", "usage_event_type")

    op.drop_index(
        "ix_nlp_capability_usage_items_meter_created",
        table_name="nlp_capability_usage_items",
    )
    op.drop_table("nlp_capability_usage_items")

    op.drop_index(
        "ix_nlp_cap_usage_res_occurred",
        table_name="nlp_capability_usage_events",
    )
    op.drop_index(
        "ix_nlp_cap_usage_status_occurred",
        table_name="nlp_capability_usage_events",
    )
    op.drop_index(
        "ix_nlp_cap_usage_provider_occurred",
        table_name="nlp_capability_usage_events",
    )
    op.drop_index(
        "ix_nlp_cap_usage_type_occurred",
        table_name="nlp_capability_usage_events",
    )
    op.drop_index(
        "ix_nlp_cap_usage_workspace_occurred",
        table_name="nlp_capability_usage_events",
    )
    op.drop_index(
        "ix_nlp_cap_usage_user_occurred",
        table_name="nlp_capability_usage_events",
    )
    op.drop_table("nlp_capability_usage_events")

    op.drop_index(
        "ix_nlp_meter_pricing_rules_status_eff",
        table_name="nlp_meter_pricing_rules",
    )
    op.drop_index(
        "ix_nlp_meter_pricing_rules_key_eff",
        table_name="nlp_meter_pricing_rules",
    )
    op.drop_table("nlp_meter_pricing_rules")

    op.drop_column("nlp_usage_events", "usage_details_json")
    op.drop_column("nlp_usage_events", "image_input_tokens")
    op.drop_column("nlp_usage_events", "text_input_tokens")
