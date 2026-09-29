"""merge the whiteboard catalog and transcript timestamp migration heads."""

from alembic import op


revision = "20260929_47_merge_wb_transcript"
down_revision = ("20260929_45_wb_catalog", "20260929_46_transcript_time")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
