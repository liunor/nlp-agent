"""add manual-title flag and summary lease

Revision ID: 20260830_38_session_title_manual
Revises: 20260829_37_session_summary
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.mysql import DATETIME


revision = "20260830_38_session_title_manual"
down_revision = "20260829_37_session_summary"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = inspect(op.get_bind())
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    # ``title_is_manual`` records a user rename so the LLM summarizer never
    # overwrites it.  ``summary_lease_expires_at`` is the short-lived claim the
    # summarizer takes before paying for an LLM call, deduplicating concurrent
    # workers.  Both are guarded because a concurrent migration may already
    # have introduced them.
    if not _has_column("nlp_conversations", "title_is_manual"):
        op.add_column(
            "nlp_conversations",
            sa.Column("title_is_manual", sa.Boolean(), nullable=False, server_default="0"),
        )
    if not _has_column("nlp_conversations", "summary_lease_expires_at"):
        op.add_column(
            "nlp_conversations",
            sa.Column("summary_lease_expires_at", DATETIME(fsp=6), nullable=True),
        )


def downgrade() -> None:
    if _has_column("nlp_conversations", "summary_lease_expires_at"):
        op.drop_column("nlp_conversations", "summary_lease_expires_at")
    if _has_column("nlp_conversations", "title_is_manual"):
        op.drop_column("nlp_conversations", "title_is_manual")
