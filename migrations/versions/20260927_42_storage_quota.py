"""add account-wide storage ledger and reservations"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import BIGINT, DATETIME


revision = "20260927_42_storage_quota"
# Keep this feature migration self-contained. The whiteboard migrations are
# maintained separately and are not required to create the storage ledger.
down_revision = "20260927_40_user_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid_type = sa.String(36, collation="ascii_bin")
    op.create_table(
        "nlp_storage_accounts",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("owner_user_id", uuid_type, nullable=False),
        sa.Column("core_used_bytes", BIGINT(unsigned=True), nullable=False, server_default="0"),
        sa.Column("files_used_bytes", BIGINT(unsigned=True), nullable=False, server_default="0"),
        sa.Column("core_reserved_bytes", BIGINT(unsigned=True), nullable=False, server_default="0"),
        sa.Column("files_reserved_bytes", BIGINT(unsigned=True), nullable=False, server_default="0"),
        sa.Column("core_quota_override_bytes", BIGINT(unsigned=True), nullable=True),
        sa.Column("files_quota_override_bytes", BIGINT(unsigned=True), nullable=True),
        sa.Column("max_file_override_bytes", BIGINT(unsigned=True), nullable=True),
        sa.Column("max_items_override", BIGINT(unsigned=True), nullable=True),
        sa.Column("last_reconciled_at", DATETIME(fsp=6), nullable=True),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.ForeignKeyConstraint(["owner_user_id"], ["nlp_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_user_id", name="uq_nlp_storage_accounts_owner_user_id"),
    )
    op.create_table(
        "nlp_storage_reservations",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("owner_user_id", uuid_type, nullable=False),
        sa.Column("bucket", sa.String(16), nullable=False),
        sa.Column("amount_bytes", BIGINT(unsigned=True), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_key", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="reserved"),
        sa.Column("reserved_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Column("finalized_at", DATETIME(fsp=6), nullable=True),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.ForeignKeyConstraint(["owner_user_id"], ["nlp_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_nlp_storage_reservations_owner_status",
        "nlp_storage_reservations",
        ["owner_user_id", "status"],
    )
    op.create_index(
        "ix_nlp_storage_reservations_created",
        "nlp_storage_reservations",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_nlp_storage_reservations_created", table_name="nlp_storage_reservations")
    op.drop_index("ix_nlp_storage_reservations_owner_status", table_name="nlp_storage_reservations")
    op.drop_table("nlp_storage_reservations")
    op.drop_table("nlp_storage_accounts")
