"""add a singleton lock row for shared storage-pool reservations"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME, SMALLINT


revision = "20260927_44_storage_pool_lock"
down_revision = "20260927_43_storage_quota_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nlp_storage_pool_lock",
        sa.Column("id", SMALLINT(unsigned=True), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(sa.text("INSERT INTO nlp_storage_pool_lock (id) VALUES (1)"))


def downgrade() -> None:
    op.drop_table("nlp_storage_pool_lock")
