"""store named whiteboard teaching materials"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME, JSON


revision = "20260927_41_whiteboard_library"
down_revision = "20260927_40_user_files"
branch_labels = None
depends_on = None


def _expand_alembic_version_column() -> None:
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(32),
        type_=sa.String(64),
        existing_nullable=False,
    )


def upgrade() -> None:
    _expand_alembic_version_column()
    op.create_table(
        "nlp_whiteboard_library_items",
        sa.Column("id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("item_json", JSON, nullable=False),
        sa.Column("created_by", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.PrimaryKeyConstraint("id", name="pk_nlp_whiteboard_library_items"),
        comment="可在教材中关联的命名白板素材。",
    )
    op.create_index(
        "ix_nlp_whiteboard_library_created",
        "nlp_whiteboard_library_items",
        ["created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_nlp_whiteboard_library_created", table_name="nlp_whiteboard_library_items")
    op.drop_table("nlp_whiteboard_library_items")
