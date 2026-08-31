"""add summary retry tracking

Revision ID: 20260831_39_summary_backoff
Revises: 20260830_38_session_title_manual
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260831_39_summary_backoff"
down_revision = "20260830_38_session_title_manual"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = inspect(op.get_bind())
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    # ``summary_attempts`` counts how many LLM calls have been made for the
    # first summary.  The sweep uses it to cap retries (preventing a retry
    # storm while the model is unavailable) and to compute exponential backoff.
    if not _has_column("nlp_conversations", "summary_attempts"):
        op.add_column(
            "nlp_conversations",
            sa.Column("summary_attempts", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    if _has_column("nlp_conversations", "summary_attempts"):
        op.drop_column("nlp_conversations", "summary_attempts")
