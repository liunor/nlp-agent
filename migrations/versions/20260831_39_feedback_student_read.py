"""track the student's read position for developer feedback replies"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME


# Keep the revision within Alembic's 32-character version column limit.
revision = "20260831_39_feedback_student"
down_revision = "20260831_38_feedback_write"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "nlp_feedback_threads",
        sa.Column("student_read_at", DATETIME(fsp=6), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("nlp_feedback_threads", "student_read_at")
