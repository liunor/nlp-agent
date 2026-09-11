"""Persist Provider-reported cache misses on usage events."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


# Alembic stores revision IDs in a VARCHAR(32) column.
revision = "20260909_52_cache_miss_tokens"
down_revision = "20260907_51_merge_database_heads"
branch_labels = None
depends_on = None


def _column_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns("nlp_usage_events")}


def upgrade() -> None:
    if "cache_miss_input_tokens" not in _column_names():
        op.add_column(
            "nlp_usage_events",
            sa.Column(
                "cache_miss_input_tokens",
                mysql.BIGINT(unsigned=True),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )


def downgrade() -> None:
    if "cache_miss_input_tokens" in _column_names():
        op.drop_column("nlp_usage_events", "cache_miss_input_tokens")
