"""add LLM-generated session title basis timestamp

Revision ID: 20260829_37_session_summary
Revises: 20260829_36_usage_indexes
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.mysql import DATETIME


revision = "20260829_37_session_summary"
down_revision = "20260829_36_usage_indexes"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = inspect(op.get_bind())
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    # ``title`` already exists (``server_default=""``); it stays empty until the
    # LLM summarizer writes the generated topic.  ``title_updated_at`` records
    # the *basis* of the last summary (the newest completed turn's
    # ``completed_at``), so a conditional UPDATE can reject out-of-order writes
    # without a second column.
    #
    # The add is guarded because a concurrent migration may already have
    # introduced this column; re-adding it fails CI with ``1060 Duplicate
    # column name``.
    if not _has_column("nlp_conversations", "title_updated_at"):
        op.add_column(
            "nlp_conversations",
            sa.Column("title_updated_at", DATETIME(fsp=6), nullable=True),
        )


def downgrade() -> None:
    if _has_column("nlp_conversations", "title_updated_at"):
        op.drop_column("nlp_conversations", "title_updated_at")
