"""store long-form knowledge book drafts and published pages

Revision ID: 20260827_30_book_pages
Revises: 20260820_24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, MEDIUMTEXT


revision = "20260827_30_book_pages"
down_revision = "20260820_24"
branch_labels = None
depends_on = None


UUID = sa.String(36, collation="ascii_bin")


def upgrade() -> None:
    op.create_table(
        "nlp_knowledge_pages",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "workspace_id",
            UUID,
            sa.ForeignKey("nlp_course_catalogs.workspace_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("knowledge_point_id", UUID, nullable=False),
        # MySQL 8.4 rejects defaults on TEXT/MEDIUMTEXT columns.  The
        # repository always supplies an explicit empty string for a new draft.
        sa.Column("draft_markdown", MEDIUMTEXT(), nullable=False),
        sa.Column("published_markdown", MEDIUMTEXT(), nullable=True),
        sa.Column("revision", BIGINT(unsigned=True), nullable=False, server_default="0"),
        sa.Column("published_revision", BIGINT(unsigned=True), nullable=True),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.UniqueConstraint(
            "workspace_id",
            "knowledge_point_id",
            name="uq_nlp_knowledge_pages_workspace_point",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
        comment="知识点教材正文的草稿与已发布版本。",
    )


def downgrade() -> None:
    op.drop_table("nlp_knowledge_pages")
