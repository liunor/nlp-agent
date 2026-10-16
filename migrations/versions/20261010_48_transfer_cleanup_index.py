"""Add an index for bounded file-transfer message cleanup."""

import sqlalchemy as sa
from alembic import context, op


revision = "20261010_48_cleanup_index"
down_revision = "20261009_47_transfer_notify_seq"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_nlp_file_transfers_cleanup_status_created"


def _index_exists() -> bool:
    if context.is_offline_mode():
        return False
    return any(
        index["name"] == _INDEX_NAME
        for index in sa.inspect(op.get_bind()).get_indexes("nlp_file_transfers")
    )


def upgrade() -> None:
    if not _index_exists():
        op.create_index(
            _INDEX_NAME,
            "nlp_file_transfers",
            ["status", "created_at", "id"],
        )


def downgrade() -> None:
    if context.is_offline_mode() or _index_exists():
        op.drop_index(_INDEX_NAME, table_name="nlp_file_transfers")
