"""Persist release announcement read receipts per account."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "20261117_release_note_reads"
down_revision = "20261102_add_workspace_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nlp_release_note_reads",
        sa.Column("user_id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("release_note_id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("read_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "release_note_id"),
        sa.ForeignKeyConstraint(["user_id"], ["nlp_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["release_note_id"], ["nlp_release_notes.id"], ondelete="CASCADE"),
        comment="用户发布说明已读记录。",
    )


def downgrade() -> None:
    op.drop_table("nlp_release_note_reads")
