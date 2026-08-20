"""merge migration heads: feature (user_soft_delete) + develop (drop_feedback).

feature/teacher-question-stats was merged with develop, producing two
divergent heads from the common ancestor 20260813_16:
  - develop head : 20260818_19_drop_feedback
  - feature head : 20260818_20_user_soft_delete
This empty merge unifies them into a single head so ``alembic upgrade head``
succeeds.

Revision ID: 20260818_21
Revises: 20260818_19, 20260818_20
"""

from alembic import op

revision = "20260818_21"
down_revision = ("20260818_19", "20260818_20")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
