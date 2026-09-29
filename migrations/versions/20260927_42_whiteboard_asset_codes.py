"""add human-readable codes to shared whiteboard materials"""

from alembic import op
import sqlalchemy as sa


revision = "20260927_42_wb_asset_codes"
down_revision = "20260910_53_whiteboard_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "nlp_whiteboard_library_items",
        sa.Column("asset_code", sa.String(32), nullable=True),
    )
    op.execute(
        "UPDATE nlp_whiteboard_library_items "
        "SET asset_code=CONCAT('WB-', UPPER(REPLACE(LEFT(id, 8), '-', ''))) "
        "WHERE asset_code IS NULL"
    )
    op.alter_column(
        "nlp_whiteboard_library_items",
        "asset_code",
        existing_type=sa.String(32),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_nlp_whiteboard_library_items_asset_code",
        "nlp_whiteboard_library_items",
        ["asset_code"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_nlp_whiteboard_library_items_asset_code",
        "nlp_whiteboard_library_items",
        type_="unique",
    )
    op.drop_column("nlp_whiteboard_library_items", "asset_code")
