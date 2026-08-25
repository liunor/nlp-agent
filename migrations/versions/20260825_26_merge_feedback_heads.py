"""merge feedback thread and menu migration heads."""

from alembic import op


revision = "20260825_26"
down_revision = ("20260819_20", "20260820_25")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
