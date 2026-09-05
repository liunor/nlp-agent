"""Add metering facts and configurable prices for billable tool features."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


revision = "20260904_49_billable_features"
down_revision = "20260904_48_developer_merge"
branch_labels = None
depends_on = None


_PRICING_COLUMNS = (
    "visual_input_credits_micro_per_million_tokens",
    "image_unit_credits_micro",
    "search_call_credits_micro",
    "link_page_credits_micro",
)

_USAGE_COLUMNS = (
    "visual_input_tokens",
    "image_units",
    "search_calls",
    "link_pages",
)


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    pricing_columns = _column_names("nlp_pricing_rules")
    for name in _PRICING_COLUMNS:
        if name not in pricing_columns:
            op.add_column(
                "nlp_pricing_rules",
                sa.Column(name, mysql.BIGINT(unsigned=True), nullable=True),
            )

    usage_columns = _column_names("nlp_usage_events")
    for name in _USAGE_COLUMNS:
        if name not in usage_columns:
            op.add_column(
                "nlp_usage_events",
                sa.Column(
                    name,
                    mysql.BIGINT(unsigned=True),
                    nullable=False,
                    server_default=sa.text("0"),
                ),
            )


def downgrade() -> None:
    usage_columns = _column_names("nlp_usage_events")
    for name in reversed(_USAGE_COLUMNS):
        if name in usage_columns:
            op.drop_column("nlp_usage_events", name)

    pricing_columns = _column_names("nlp_pricing_rules")
    for name in reversed(_PRICING_COLUMNS):
        if name in pricing_columns:
            op.drop_column("nlp_pricing_rules", name)
