"""merge the session-summary migration branch into the released application head."""

from alembic import op


revision = "20260831_40_summary_merge"
down_revision = ("20260831_39_feedback_student", "20260831_39_summary_backoff")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass