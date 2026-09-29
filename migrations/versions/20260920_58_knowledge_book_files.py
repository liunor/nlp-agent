"""store files embedded in teacher-authored knowledge-book pages"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, LONGBLOB


revision = "20260920_58_knowledge_book_files"
down_revision = "20260916_57_rbac_menu_cleanup"
branch_labels = None
depends_on = None


UUID = sa.String(36, collation="ascii_bin")


def upgrade() -> None:
    op.create_table(
        "nlp_knowledge_book_files",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "workspace_id",
            UUID,
            sa.ForeignKey("nlp_course_catalogs.workspace_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("knowledge_point_id", UUID, nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("content", LONGBLOB(), nullable=False),
        sa.Column("size_bytes", BIGINT(unsigned=True), nullable=False),
        sa.Column("sha256", sa.String(64, collation="ascii_bin"), nullable=False),
        sa.Column("created_by", sa.String(128, collation="ascii_bin"), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Index(
            "ix_nlp_knowledge_book_files_point",
            "workspace_id",
            "knowledge_point_id",
            "created_at",
            "id",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
        comment="教师教材中可预览、可下载的文件内容。",
    )
    op.create_table(
        "nlp_knowledge_book_file_refs",
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("knowledge_point_id", UUID, nullable=False),
        sa.Column("file_id", UUID, nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.PrimaryKeyConstraint(
            "workspace_id",
            "knowledge_point_id",
            "state",
            "file_id",
            name="pk_nlp_knowledge_book_file_refs",
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["nlp_knowledge_book_files.id"],
            ondelete="CASCADE",
            name="fk_nlp_book_file_refs_file",
        ),
        sa.CheckConstraint("state IN ('draft', 'published')", name="ck_nlp_book_file_refs_state"),
        sa.Index(
            "ix_nlp_knowledge_book_file_refs_file",
            "workspace_id",
            "file_id",
            "state",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
        comment="知识教材文件的草稿与已发布引用关系。",
    )


def downgrade() -> None:
    op.drop_table("nlp_knowledge_book_file_refs")
    op.drop_table("nlp_knowledge_book_files")
