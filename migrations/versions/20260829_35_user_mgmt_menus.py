"""backfill the user-management developer menu projection.

The five user-management menus (users / roles / menus / audit / sessions)
were added to ``MENU_CATALOG`` after migration ``20260819_22`` had already
seeded the control-plane menu projection on existing databases.  Fresh
databases pick them up through migration 22 (which inserts the full current
catalog), but databases upgraded before the catalog entries existed are
missing them, so ``/api/v1/system/menus/visible`` never returns the
user-management entries and the developer workspace hides those pages.
"""

from alembic import context, op
import sqlalchemy as sa

from migrations.rbac_seed_data import Permission
from migrations.rbac_seed_data import menu_id, menu_row, role_id


revision = "20260829_35_user_mgmt_menus"
down_revision = "20260828_34_auth_codes"
branch_labels = None
depends_on = None


USER_MANAGEMENT_MENU_ITEMS = (
    ("developer.users", "用户管理", "/developer/users", "users", Permission.SYSTEM_USER_MANAGE, 100),
    ("developer.roles", "角色权限", "/developer/roles", "roles", Permission.SYSTEM_ROLE_MANAGE, 110),
    ("developer.menus", "菜单管理", "/developer/menus", "menus", Permission.SYSTEM_ROLE_MANAGE, 120),
    ("developer.audit", "审计日志", "/developer/audit", "audit", Permission.SYSTEM_AUDIT_READ, 130),
    ("developer.sessions", "Agent 会话", "/developer/sessions", "sessions", Permission.AGENT_SESSION_READ, 140),
)


def _user_management_items() -> list[tuple[str, str, str, str, object, int]]:
    # Keep this historical projection frozen.  Reading the mutable current
    # catalog here would make a fresh upgrade depend on whichever menu fields
    # happen to exist in the application source years later.
    return list(USER_MANAGEMENT_MENU_ITEMS)


def upgrade() -> None:
    # ``bulk_insert`` only emits columns declared on the lightweight table
    # object.  Keep this projection in sync with ``MenuModel``; declaring just
    # ``id`` silently dropped all required menu fields and failed on MySQL's
    # strict mode (same failure mode migration 20260825_27 documents).
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
        # Migration 22 already emits the full catalog for a clean offline
        # script; this revision only backfills databases upgraded before the
        # user-management catalog entries existed.
        return
    bind = op.get_bind()
    developer = role_id("developer")
    for item in _user_management_items():
        menu = menu_row(item)
        if bind.execute(sa.select(menus.c.id).where(menus.c.id == menu["id"])).first() is None:
            op.bulk_insert(menus, [menu])
        role_menu = {"role_id": developer, "menu_id": menu_id(item[0])}
        if bind.execute(
            sa.select(role_menus.c.menu_id).where(
                role_menus.c.role_id == role_menu["role_id"],
                role_menus.c.menu_id == role_menu["menu_id"],
            )
        ).first() is None:
            op.bulk_insert(role_menus, [role_menu])


def downgrade() -> None:
    # This is a data backfill, not ownership of the projection rows.  Some of
    # these rows may have existed before this revision (or may have been
    # edited by an administrator), so deleting them on downgrade could remove
    # valid user data and break the developer navigation.  Leaving the
    # idempotent projection in place is the only safe downgrade behavior.
    pass
