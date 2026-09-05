"""Move authorization audit navigation from the developer plane to Monitor."""

from alembic import context, op
import sqlalchemy as sa

from core.rbac import Permission
from server.rbac.catalog import MENU_CATALOG, menu_id, menu_row, role_id


revision = "20260901_41_monitor_audit"
down_revision = "20260831_40_summary_merge"
branch_labels = None
depends_on = None


def _tables():
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
    return menus, role_menus


def upgrade() -> None:
    item = next(item for item in MENU_CATALOG if item[0] == "monitor.audit")
    new_menu = menu_row(item)
    old_menu_id = menu_id("developer.audit")
    menus, role_menus = _tables()

    if context.is_offline_mode():
        # A clean offline build already received the new catalog entry from the
        # foundation migration. Remove the historical developer projection.
        op.execute(sa.text("DELETE FROM nlp_role_menus WHERE menu_id = :menu_id").bindparams(menu_id=old_menu_id))
        op.execute(sa.text("DELETE FROM nlp_menus WHERE id = :menu_id").bindparams(menu_id=old_menu_id))
        return

    bind = op.get_bind()
    new_exists = bind.execute(sa.select(menus.c.id).where(menus.c.id == new_menu["id"])).first() is not None
    old_exists = bind.execute(sa.select(menus.c.id).where(menus.c.id == old_menu_id)).first() is not None

    if not new_exists:
        op.bulk_insert(menus, [new_menu])
        op.bulk_insert(role_menus, [{"role_id": role_id("developer"), "menu_id": new_menu["id"]}])
    if old_exists:
        if new_exists:
            op.execute(sa.delete(role_menus).where(role_menus.c.menu_id == old_menu_id))
            op.execute(sa.delete(menus).where(menus.c.id == old_menu_id))
        else:
            # Preserve a custom installation's existing binding while moving
            # the menu into the monitor client scope.
            op.execute(
                sa.update(menus)
                .where(menus.c.id == old_menu_id)
                .values(route_path=new_menu["route_path"], client_scope="monitor", sort_order=new_menu["sort_order"])
            )


def downgrade() -> None:
    new_menu_id = menu_id("monitor.audit")
    legacy_item = ("developer.audit", "审计日志", "/developer/audit", "audit", Permission.SYSTEM_AUDIT_READ, 130)
    menus, role_menus = _tables()
    op.execute(sa.text("DELETE FROM nlp_role_menus WHERE menu_id = :menu_id").bindparams(menu_id=new_menu_id))
    op.execute(sa.text("DELETE FROM nlp_menus WHERE id = :menu_id").bindparams(menu_id=new_menu_id))
    if context.is_offline_mode():
        op.bulk_insert(menus, [menu_row(legacy_item)])
        op.bulk_insert(role_menus, [{"role_id": role_id("developer"), "menu_id": menu_id("developer.audit")}])
        return
    bind = op.get_bind()
    old_menu_id = menu_id("developer.audit")
    if bind.execute(sa.select(menus.c.id).where(menus.c.id == old_menu_id)).first() is None:
        op.bulk_insert(menus, [menu_row(legacy_item)])
    else:
        op.execute(
            sa.update(menus)
            .where(menus.c.id == old_menu_id)
            .values(route_path="/developer/audit", client_scope="developer", sort_order=130)
        )
    if bind.execute(sa.select(role_menus.c.menu_id).where(role_menus.c.role_id == role_id("developer"), role_menus.c.menu_id == old_menu_id)).first() is None:
        op.bulk_insert(role_menus, [{"role_id": role_id("developer"), "menu_id": old_menu_id}])
