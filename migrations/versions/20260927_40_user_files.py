"""add account-owned file and folder storage"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import BIGINT, DATETIME


revision = "20260927_40_user_files"
down_revision = "20260831_39_feedback_student"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nlp_user_files",
        sa.Column("id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("owner_user_id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("workspace_id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("parent_id", sa.String(36, collation="ascii_bin"), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=True),
        sa.Column("mime_type", sa.String(128), nullable=True),
        sa.Column("size_bytes", BIGINT(unsigned=True), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("deleted_at", DATETIME(fsp=6), nullable=True),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.ForeignKeyConstraint(["owner_user_id"], ["nlp_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["nlp_workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_nlp_user_files_storage_key"),
    )
    op.create_index(
        "ix_nlp_user_files_owner_workspace_parent",
        "nlp_user_files",
        ["owner_user_id", "workspace_id", "parent_id"],
    )
    op.create_index(
        "ix_nlp_user_files_owner_workspace_status",
        "nlp_user_files",
        ["owner_user_id", "workspace_id", "status"],
    )
    op.create_index("ix_nlp_user_files_parent_id", "nlp_user_files", ["parent_id"])
    op.create_index("ix_nlp_user_files_deleted_at", "nlp_user_files", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_nlp_user_files_deleted_at", table_name="nlp_user_files")
    op.drop_index("ix_nlp_user_files_parent_id", table_name="nlp_user_files")
    op.drop_index("ix_nlp_user_files_owner_workspace_status", table_name="nlp_user_files")
    op.drop_index("ix_nlp_user_files_owner_workspace_parent", table_name="nlp_user_files")
    op.drop_table("nlp_user_files")
