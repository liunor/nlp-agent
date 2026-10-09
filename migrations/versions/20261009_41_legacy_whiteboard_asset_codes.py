"""Compatibility bridge for the historical whiteboard asset-code revision."""

from alembic import context, op
import sqlalchemy as sa


revision = "20260927_42_wb_asset_codes"
down_revision = "20260910_53_whiteboard_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        op.execute(
            "ALTER TABLE nlp_whiteboard_library_items "
            "ADD COLUMN IF NOT EXISTS asset_code VARCHAR(32) NULL"
        )
        op.execute(
            "UPDATE nlp_whiteboard_library_items SET asset_code="
            "CONCAT('WB-', UPPER(REPLACE(LEFT(id, 8), '-', ''))) "
            "WHERE asset_code IS NULL"
        )
        op.execute("ALTER TABLE nlp_whiteboard_library_items MODIFY asset_code VARCHAR(32) NOT NULL")
        op.execute(
            "ALTER TABLE nlp_whiteboard_library_items "
            "ADD UNIQUE INDEX uq_nlp_whiteboard_library_items_asset_code (asset_code)"
        )
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "nlp_whiteboard_library_items" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("nlp_whiteboard_library_items")}
    if "asset_code" not in columns:
        op.add_column(
            "nlp_whiteboard_library_items",
            sa.Column("asset_code", sa.String(32), nullable=True),
        )
        op.execute(
            "UPDATE nlp_whiteboard_library_items SET asset_code="
            "CONCAT('WB-', UPPER(REPLACE(LEFT(id, 8), '-', ''))) "
            "WHERE asset_code IS NULL"
        )
        op.alter_column(
            "nlp_whiteboard_library_items",
            "asset_code",
            existing_type=sa.String(32),
            nullable=False,
        )
    unique_names = {
        constraint.get("name")
        for constraint in inspector.get_unique_constraints("nlp_whiteboard_library_items")
    }
    unique_names.update(
        index.get("name")
        for index in inspector.get_indexes("nlp_whiteboard_library_items")
        if index.get("unique")
    )
    if "uq_nlp_whiteboard_library_items_asset_code" not in unique_names:
        op.create_unique_constraint(
            "uq_nlp_whiteboard_library_items_asset_code",
            "nlp_whiteboard_library_items",
            ["asset_code"],
        )


def downgrade() -> None:
    pass
