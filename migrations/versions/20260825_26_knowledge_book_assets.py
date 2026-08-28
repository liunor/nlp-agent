"""store validated knowledge-book image assets

Revision ID: 20260827_31_book_assets
Revises: 20260827_30_book_pages
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, LONGBLOB


revision = "20260827_31_book_assets"
down_revision = "20260827_30_book_pages"
branch_labels = None
depends_on = None


UUID = sa.String(36, collation="ascii_bin")


def upgrade() -> None:
    op.create_table(
        "nlp_knowledge_book_assets",
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("asset_path", sa.String(512, collation="utf8mb4_bin"), nullable=False),
        sa.Column("media_type", sa.String(64), nullable=False),
        sa.Column("draft_content", LONGBLOB(), nullable=False),
        sa.Column("published_content", LONGBLOB(), nullable=True),
        sa.Column("size_bytes", BIGINT(unsigned=True), nullable=False),
        sa.Column("sha256", sa.String(64, collation="ascii_bin"), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.PrimaryKeyConstraint("workspace_id", "asset_path", name="pk_nlp_knowledge_book_assets"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["nlp_course_catalogs.workspace_id"],
            ondelete="CASCADE",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
        comment="知识教材批量导入后经过校验的图片资源。",
    )


def downgrade() -> None:
    op.drop_table("nlp_knowledge_book_assets")
