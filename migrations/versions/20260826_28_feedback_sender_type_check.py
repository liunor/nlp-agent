"""feedback sender_type check constraint.

``nlp_feedback_messages.sender_type`` was a plain ``VARCHAR(16)``; any value
could be written, silently breaking the unread aggregation (which filters on
``sender_type = 'student'``) and the developer-workspace rendering (anything
non-``student`` is shown as "开发者").  Enforce the two-value vocabulary at
the database layer.

Fail-fast policy: no data cleanup runs here.  If existing rows carry values
outside ``('student', 'developer')``, MySQL rejects the constraint and the
migration rolls back — inspect and fix those rows manually, then re-run.
Requires MySQL 8.0.16+ for enforced CHECK constraints (the project already
requires MySQL 8 for window functions).

Revision ID: 20260826_28
Revises: 20260826_27
"""

from alembic import op
import sqlalchemy as sa


revision = "20260826_28"
down_revision = "20260826_27"
branch_labels = None
depends_on = None

# Explicit DDL on purpose: the migration env mounts the ORM naming convention,
# which would double-prefix a name passed to op.create_check_constraint.  This
# keeps the database object identical to the create_all path
# (ck_nlp_feedback_messages_sender_type).
CHECK_NAME = "ck_nlp_feedback_messages_sender_type"


def upgrade() -> None:
    op.execute(
        sa.text(
            f"ALTER TABLE nlp_feedback_messages "
            f"ADD CONSTRAINT {CHECK_NAME} "
            f"CHECK (sender_type IN ('student', 'developer'))"
        )
    )


def downgrade() -> None:
    # DROP CHECK works on every MySQL version with enforced CHECK (8.0.16+);
    # the generic DROP CONSTRAINT arrived only in 8.0.19.
    op.execute(sa.text(f"ALTER TABLE nlp_feedback_messages DROP CHECK {CHECK_NAME}"))
