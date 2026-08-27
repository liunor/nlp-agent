"""seed the Phase 3 sandbox developer menu projection."""

from alembic import context, op
import sqlalchemy as sa

from server.rbac.catalog import MENU_CATALOG, menu_id, menu_row, role_id


revision = "20260825_27"
down_revision = "20260825_26_sandbox_warm_pool"
branch_labels = None
depends_on = None


def upgrade() -> None:
    item = next(item for item in MENU_CATALOG if item[0] == "developer.sandbox")
    # ``bulk_insert`` only emits columns declared on the lightweight table
    # object.  Keep this projection in sync with ``MenuModel``; declaring just
    # ``id`` silently dropped all required menu fields and failed on MySQL's
    # strict mode with ``Field 'menu_type' doesn't have a default value``.
    menus = sa.table(
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
    role_menus = sa.table(
        "nlp_role_menus",
        sa.column("role_id", sa.String()),
        sa.column("menu_id", sa.String()),
    )
    if context.is_offline_mode():
        # The earlier RBAC migration already emits the full catalog for a
        # clean offline script.  This revision only backfills databases that
        # were upgraded before the sandbox catalog entry existed.
        return
    bind = op.get_bind()
    menu = menu_row(item)
    if bind.execute(sa.select(menus.c.id).where(menus.c.id == menu["id"])).first() is None:
        op.bulk_insert(menus, [menu])
    role_menu = {"role_id": role_id("developer"), "menu_id": menu_id(item[0])}
    if bind.execute(
        sa.select(role_menus.c.menu_id).where(
            role_menus.c.role_id == role_menu["role_id"],
            role_menus.c.menu_id == role_menu["menu_id"],
        )
    ).first() is None:
        op.bulk_insert(role_menus, [role_menu])


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM nlp_role_menus WHERE role_id = :role_id AND menu_id = :menu_id").bindparams(
            role_id=role_id("developer"), menu_id=menu_id("developer.sandbox")
        )
    )
    op.execute(sa.text("DELETE FROM nlp_menus WHERE id = :menu_id").bindparams(menu_id=menu_id("developer.sandbox")))
