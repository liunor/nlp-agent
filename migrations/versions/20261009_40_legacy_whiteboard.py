"""Compatibility bridge for the historically deployed whiteboard revision.

The original revision id is intentionally preserved so databases that already
recorded it can continue upgrading.  This bridge is idempotent because some
early environments created the table before recording the revision.
"""

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME


revision = "20260910_53_whiteboard_library"
down_revision = None
branch_labels = None
depends_on = None

UUID = sa.String(36, collation="ascii_bin")


def upgrade() -> None:
    if context.is_offline_mode():
        op.execute(
            "CREATE TABLE IF NOT EXISTS nlp_whiteboard_library_items ("
            "id VARCHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, "
            "name VARCHAR(128) NOT NULL, item_json JSON NOT NULL, "
            "created_by VARCHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, "
            "created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), "
            "updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), "
            "PRIMARY KEY (id), INDEX ix_nlp_whiteboard_library_created (created_at, id)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
        )
        return

    bind = op.get_bind()
    if "nlp_whiteboard_library_items" in sa.inspect(bind).get_table_names():
        return
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
        comment="可在知识教材中关联的命名白板素材。",
    )


def downgrade() -> None:
    # A compatibility bridge must never remove data from an already deployed DB.
    pass
