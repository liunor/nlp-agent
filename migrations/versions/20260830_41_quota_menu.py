"""Backfill the developer quota-management permission and menu projection."""

from alembic import context, op
import sqlalchemy as sa

from core.rbac import Permission
from server.rbac.catalog import MENU_CATALOG, menu_id, menu_row, permission_id, permission_row, role_id


revision = "20260830_41_quota_menu"
down_revision = "20260830_40_quota_phase3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The fresh-install RBAC migration imports the current catalog and thus
    # already emits these rows in offline SQL.  An offline script cannot
    # perform the existence checks used for an upgrade of an older database;
    # emitting duplicate unique-key inserts would make the generated script
    # invalid.  Existing deployments use the online idempotent backfill below.
    if context.is_offline_mode():
        return
    quota_permissions = (Permission.SYSTEM_QUOTA_READ, Permission.SYSTEM_QUOTA_MANAGE)
    permission_rows = [permission_row(item) for item in quota_permissions]
    menu = menu_row(next(item for item in MENU_CATALOG if item[0] == "developer.quotas"))
    permissions = sa.table("nlp_permissions", sa.column("id", sa.String()), sa.column("code", sa.String()), sa.column("domain_name", sa.String()), sa.column("resource_name", sa.String()), sa.column("action_name", sa.String()), sa.column("name", sa.String()), sa.column("description", sa.String()), sa.column("status", sa.String()), sa.column("is_builtin", sa.Boolean()))
    menus = sa.table("nlp_menus", sa.column("id", sa.String()), sa.column("parent_id", sa.String()), sa.column("menu_type", sa.String()), sa.column("name", sa.String()), sa.column("route_path", sa.String()), sa.column("component_key", sa.String()), sa.column("permission_id", sa.String()), sa.column("client_scope", sa.String()), sa.column("sort_order", sa.Integer()), sa.column("visible", sa.Boolean()), sa.column("status", sa.String()))
    role_permissions = sa.table("nlp_role_permissions", sa.column("role_id", sa.String()), sa.column("permission_id", sa.String()))
    role_menus = sa.table("nlp_role_menus", sa.column("role_id", sa.String()), sa.column("menu_id", sa.String()))
    role_scopes = sa.table("nlp_role_permission_scopes", sa.column("role_id", sa.String()), sa.column("permission_id", sa.String()), sa.column("scope_type", sa.String()))
    bind = op.get_bind()
    for permission in permission_rows:
        if bind.execute(sa.select(permissions.c.id).where(permissions.c.id == permission["id"])).first() is None:
            op.bulk_insert(permissions, [permission])
    if bind.execute(sa.select(menus.c.id).where(menus.c.id == menu["id"])).first() is None:
        op.bulk_insert(menus, [menu])
    developer_id = role_id("developer")
    for quota_permission in quota_permissions:
        perm_id = permission_id(quota_permission)
        if bind.execute(sa.select(role_permissions.c.permission_id).where(role_permissions.c.role_id == developer_id, role_permissions.c.permission_id == perm_id)).first() is None:
            op.bulk_insert(role_permissions, [{"role_id": developer_id, "permission_id": perm_id}])
        if bind.execute(sa.select(role_scopes.c.permission_id).where(role_scopes.c.role_id == developer_id, role_scopes.c.permission_id == perm_id, role_scopes.c.scope_type == "system")).first() is None:
            op.bulk_insert(role_scopes, [{"role_id": developer_id, "permission_id": perm_id, "scope_type": "system"}])
    if bind.execute(sa.select(role_menus.c.menu_id).where(role_menus.c.role_id == developer_id, role_menus.c.menu_id == menu["id"])).first() is None:
        op.bulk_insert(role_menus, [{"role_id": developer_id, "menu_id": menu["id"]}])


def downgrade() -> None:
    # Keep the projection safe for operators on downgrade; the permission and
    # menu are harmless data rows and may have been referenced by custom roles.
    pass
