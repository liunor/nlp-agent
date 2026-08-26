"""feedback list pagination indexes.

The developer feedback list is now paginated and served by aggregate queries:

- ``nlp_feedback_threads.updated_at`` — the list orders by ``updated_at DESC``
  and pages with ``LIMIT/OFFSET``; without this index every poll would sort
  the whole table.
- ``nlp_feedback_messages (thread_id, created_at)`` — the latest-message
  window function and the unread-count GROUP BY both scan one thread's
  messages ordered by ``created_at``; a plain FK index cannot serve that.

Revision ID: 20260826_27
Revises: 20260825_26
"""

from alembic import op


revision = "20260826_27"
down_revision = "20260825_26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_nlp_feedback_threads_updated_at",
        "nlp_feedback_threads",
        ["updated_at"],
    )
    op.create_index(
        "ix_nlp_feedback_messages_thread_created",
        "nlp_feedback_messages",
        ["thread_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_nlp_feedback_messages_thread_created", table_name="nlp_feedback_messages")
    op.drop_index("ix_nlp_feedback_threads_updated_at", table_name="nlp_feedback_threads")
