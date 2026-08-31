"""add workflow metadata for persistent feedback threads

The feedback feature is being linearized after the current develop head.  The
older PR used a merge migration rooted at an obsolete sandbox head; copying it
would create a second Alembic head in the knowledge-book branch.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260831_37_feedback_meta"
down_revision = "20260829_36_usage_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "nlp_feedback_threads",
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
    )
    op.add_column(
        "nlp_feedback_threads",
        sa.Column("category", sa.String(16), nullable=False, server_default="other"),
    )
    op.add_column(
        "nlp_feedback_threads",
        sa.Column("priority", sa.String(16), nullable=False, server_default="medium"),
    )
    op.create_index(
        "ix_nlp_feedback_threads_status",
        "nlp_feedback_threads",
        ["status"],
    )
    op.create_index(
        "ix_nlp_feedback_threads_category",
        "nlp_feedback_threads",
        ["category"],
    )
    op.execute(
        sa.text(
            "ALTER TABLE nlp_feedback_threads ADD CONSTRAINT "
            "ck_nlp_feedback_threads_status CHECK "
            "(status IN ('open','under_review','planned','in_progress','complete','closed'))"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE nlp_feedback_threads ADD CONSTRAINT "
            "ck_nlp_feedback_threads_category CHECK "
            "(category IN ('feature','ux','bug','other'))"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE nlp_feedback_threads ADD CONSTRAINT "
            "ck_nlp_feedback_threads_priority CHECK "
            "(priority IN ('low','medium','high'))"
        )
    )


def downgrade() -> None:
    for name in (
        "ck_nlp_feedback_threads_priority",
        "ck_nlp_feedback_threads_category",
        "ck_nlp_feedback_threads_status",
    ):
        op.execute(sa.text(f"ALTER TABLE nlp_feedback_threads DROP CHECK {name}"))
    op.drop_index("ix_nlp_feedback_threads_category", table_name="nlp_feedback_threads")
    op.drop_index("ix_nlp_feedback_threads_status", table_name="nlp_feedback_threads")
    op.drop_column("nlp_feedback_threads", "priority")
    op.drop_column("nlp_feedback_threads", "category")
    op.drop_column("nlp_feedback_threads", "status")
