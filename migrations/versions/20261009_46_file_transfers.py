"""add public identity IDs and durable file transfer requests"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import BIGINT, DATETIME


revision = "20261009_46_file_transfers"
down_revision = "20260929_45_wb_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid_type = sa.String(36, collation="ascii_bin")
    op.add_column("nlp_users", sa.Column("identity_id", sa.String(16, collation="ascii_bin"), nullable=True))
    op.get_bind().execute(sa.text("UPDATE nlp_users SET identity_id=CONCAT('NV', MOD(CONV(SUBSTRING(SHA2(id,256),1,2),16,10),8)+2, UPPER(SUBSTRING(SHA2(CONCAT('identity:',id),256),1,13))) WHERE identity_id IS NULL"))
    op.alter_column("nlp_users", "identity_id", existing_type=sa.String(16), nullable=False)
    op.create_index("ix_nlp_users_identity_id", "nlp_users", ["identity_id"], unique=True)
    op.add_column("nlp_storage_accounts", sa.Column("files_reserved_items", BIGINT(unsigned=True), nullable=False, server_default="0"))
    op.add_column("nlp_storage_reservations", sa.Column("amount_items", BIGINT(unsigned=True), nullable=False, server_default="0"))
    op.create_table(
        "nlp_file_transfers",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("sender_user_id", uuid_type, nullable=False),
        sa.Column("recipient_user_id", uuid_type, nullable=False),
        sa.Column("source_workspace_id", uuid_type, nullable=False),
        sa.Column("recipient_workspace_id", uuid_type, nullable=False),
        sa.Column("source_file_id", uuid_type),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(128)),
        sa.Column("size_bytes", BIGINT(unsigned=True), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("staging_key", sa.String(512), nullable=False, unique=True),
        sa.Column("quota_reservation_id", uuid_type, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("recipient_seen_at", DATETIME(fsp=6)),
        sa.Column("responded_at", DATETIME(fsp=6)),
        sa.Column("staging_deleted_at", DATETIME(fsp=6)),
        sa.Column("accepted_file_id", uuid_type),
        sa.Column("expires_at", DATETIME(fsp=6), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.ForeignKeyConstraint(["sender_user_id"], ["nlp_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["nlp_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_workspace_id"], ["nlp_workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recipient_workspace_id"], ["nlp_workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_file_id"], ["nlp_user_files.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["accepted_file_id"], ["nlp_user_files.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["quota_reservation_id"], ["nlp_storage_reservations.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("sender_user_id", "idempotency_key", name="uq_nlp_file_transfers_sender_idempotency"),
        comment="账户间离线文件发送请求、接收确认与暂存快照索引。",
    )
    op.create_index("ix_nlp_file_transfers_recipient_status_created", "nlp_file_transfers", ["recipient_user_id", "status", "created_at"])
    op.create_index("ix_nlp_file_transfers_sender_status_created", "nlp_file_transfers", ["sender_user_id", "status", "created_at"])
    op.create_index("ix_nlp_file_transfers_status_expires", "nlp_file_transfers", ["status", "expires_at"])


def downgrade() -> None:
    op.drop_table("nlp_file_transfers")
    op.drop_column("nlp_storage_reservations", "amount_items")
    op.drop_column("nlp_storage_accounts", "files_reserved_items")
    op.drop_index("ix_nlp_users_identity_id", table_name="nlp_users")
    op.drop_column("nlp_users", "identity_id")
