"""merge the feedback and sandbox migration heads."""

from alembic import op


revision = "20260826_29"
down_revision = ("20260826_28", "20260825_28")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
