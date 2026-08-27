"""feedback canny-lite: status/category/priority"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "20260827_29"
down_revision = "20260826_28"
branch_labels = None
depends_on = None

FEEDBACK_STATUSES = ("open", "under_review", "planned", "in_progress", "complete", "closed")
FEEDBACK_CATEGORIES = ("feature", "ux", "bug", "other")
FEEDBACK_PRIORITIES = ("low", "medium", "high")


def upgrade() -> None:
    # Add columns with server_default so existing rows are backfilled.
    op.add_column("nlp_feedback_threads", sa.Column("status", sa.String(16), nullable=False, server_default="open"))
    op.add_column("nlp_feedback_threads", sa.Column("category", sa.String(16), nullable=False, server_default="other"))
    op.add_column("nlp_feedback_threads", sa.Column("priority", sa.String(16), nullable=False, server_default="medium"))
    # Indexes for filtered list queries
    op.create_index("ix_nlp_feedback_threads_status", "nlp_feedback_threads", ["status"])
    op.create_index("ix_nlp_feedback_threads_category", "nlp_feedback_threads", ["category"])
    # CHECK constraints
    op.create_check_constraint(
        "ck_nlp_feedback_threads_status",
        "nlp_feedback_threads",
        "status IN ('open','under_review','planned','in_progress','complete','closed')",
    )
    op.create_check_constraint(
        "ck_nlp_feedback_threads_category",
        "nlp_feedback_threads",
        "category IN ('feature','ux','bug','other')",
    )
    op.create_check_constraint(
        "ck_nlp_feedback_threads_priority",
        "nlp_feedback_threads",
        "priority IN ('low','medium','high')",
    )
    # Remove server_default after backfill to keep model defaults explicit? Keep it for safety,
    # but ensure new inserts without value still get a sane default.
    # No need to drop default; SQLAlchemy will still send explicit value.


def downgrade() -> None:
    for name in ("ck_nlp_feedback_threads_priority", "ck_nlp_feedback_threads_category", "ck_nlp_feedback_threads_status"):
        try:
            op.drop_constraint(name, "nlp_feedback_threads", type_="check")
        except Exception:
            # Fallback for MySQL named CHECK drop
            op.execute(sa.text(f"ALTER TABLE nlp_feedback_threads DROP CHECK {name}"))
    op.drop_index("ix_nlp_feedback_threads_category", table_name="nlp_feedback_threads")
    op.drop_index("ix_nlp_feedback_threads_status", table_name="nlp_feedback_threads")
    op.drop_column("nlp_feedback_threads", "priority")
    op.drop_column("nlp_feedback_threads", "category")
    op.drop_column("nlp_feedback_threads", "status")
