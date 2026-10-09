"""Add an account-level monotonic version for file-transfer notifications."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import BIGINT


revision = "20261009_47_transfer_notify_seq"
down_revision = "20261009_46_file_transfers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "nlp_storage_accounts",
        sa.Column(
            "file_transfer_notification_version",
            BIGINT(unsigned=True),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("nlp_storage_accounts", "file_transfer_notification_version")
