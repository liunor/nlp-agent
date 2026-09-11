"""add globally shared whiteboard library items

Revision ID: 20260910_53_whiteboard_library
Revises: 20260909_52_cache_miss_tokens
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME


revision = "20260910_53_whiteboard_library"
down_revision = "20260909_52_cache_miss_tokens"
branch_labels = None
depends_on = None


UUID = sa.String(36, collation="ascii_bin")


def upgrade() -> None:
    op.create_table(
        "nlp_whiteboard_library_items",
        sa.Column("id", UUID, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("item_json", sa.JSON(), nullable=False),
        sa.Column("created_by", UUID, nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.PrimaryKeyConstraint("id", name="pk_nlp_whiteboard_library_items"),
        sa.Index("ix_nlp_whiteboard_library_created", "created_at", "id"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
        comment="教师和开发者创建的全局共享白板素材。",
    )


def downgrade() -> None:
    op.drop_table("nlp_whiteboard_library_items")
