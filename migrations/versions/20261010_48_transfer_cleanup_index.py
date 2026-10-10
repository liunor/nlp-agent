"""Add an index for bounded file-transfer message cleanup."""

from alembic import op


revision = "20261010_48_transfer_cleanup_index"
down_revision = "20261009_47_transfer_notify_seq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_nlp_file_transfers_cleanup_status_created",
        "nlp_file_transfers",
        ["status", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_nlp_file_transfers_cleanup_status_created",
        table_name="nlp_file_transfers",
    )
