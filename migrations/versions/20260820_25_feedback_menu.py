"""Expose feedback in the database-driven developer menu."""

from alembic import context, op
import sqlalchemy as sa

from migrations.rbac_seed_data import MENU_CATALOG, menu_id, menu_row, role_id


revision = "20260820_25"
down_revision = "20260820_24"
branch_labels = None
depends_on = "20260819_20"


def upgrade() -> None:
    item = next(item for item in MENU_CATALOG if item[0] == "developer.feedback")
    menus_table = sa.table(
        "nlp_menus",
        sa.column("id", sa.String()),
        sa.column("parent_id", sa.String()),
        sa.column("menu_type", sa.String()),
        sa.column("name", sa.String()),
        sa.column("route_path", sa.String()),
        sa.column("component_key", sa.String()),
        sa.column("permission_id", sa.String()),
        sa.column("client_scope", sa.String()),
        sa.column("sort_order", sa.Integer()),
        sa.column("visible", sa.Boolean()),
        sa.column("status", sa.String()),
    )
    role_menus_table = sa.table(
        "nlp_role_menus",
        sa.column("role_id", sa.String()),
        sa.column("menu_id", sa.String()),
    )
    menu = menu_row(item)
    binding = {"role_id": role_id("developer"), "menu_id": menu_id("developer.feedback")}
    if context.is_offline_mode():
        op.bulk_insert(menus_table, [menu])
        op.bulk_insert(role_menus_table, [binding])
        return

    bind = op.get_bind()
    if bind.execute(sa.select(menus_table.c.id).where(menus_table.c.id == menu["id"])).first() is None:
        op.bulk_insert(menus_table, [menu])
    if bind.execute(
        sa.select(role_menus_table.c.menu_id).where(
            role_menus_table.c.role_id == binding["role_id"],
            role_menus_table.c.menu_id == binding["menu_id"],
        )
    ).first() is None:
        op.bulk_insert(role_menus_table, [binding])


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM nlp_role_menus WHERE role_id = :role_id AND menu_id = :menu_id").bindparams(
            role_id=role_id("developer"), menu_id=menu_id("developer.feedback")
        )
    )
    op.execute(
        sa.text("DELETE FROM nlp_menus WHERE id = :menu_id").bindparams(
            menu_id=menu_id("developer.feedback")
        )
    )
