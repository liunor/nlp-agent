"""merge the knowledge-book migration branch into the released application head."""

from alembic import op


revision = "20260827_32_book_merge"
down_revision = ("20260826_29", "20260827_31_book_assets")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
