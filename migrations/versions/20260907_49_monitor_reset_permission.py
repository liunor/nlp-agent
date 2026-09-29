"""Add an explicit permission for the destructive monitor reset operation."""

from alembic import context, op
import sqlalchemy as sa

from core.rbac import Permission
from server.rbac.catalog import permission_id, permission_row, permission_scope, role_id


revision = "20260907_49_reset_permission"
down_revision = "20260904_49_billable_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    permissions = sa.table(
        "nlp_permissions",
        sa.column("id", sa.String()),
        sa.column("code", sa.String()),
        sa.column("domain_name", sa.String()),
        sa.column("resource_name", sa.String()),
        sa.column("action_name", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("status", sa.String()),
        sa.column("is_builtin", sa.Boolean()),
    )
    role_permissions = sa.table(
        "nlp_role_permissions",
        sa.column("role_id", sa.String()),
        sa.column("permission_id", sa.String()),
    )
    role_scopes = sa.table(
        "nlp_role_permission_scopes",
        sa.column("role_id", sa.String()),
        sa.column("permission_id", sa.String()),
        sa.column("scope_type", sa.String()),
    )
    bind = op.get_bind()
    permission = Permission.SYSTEM_RUNTIME_RESET
    permission_value = permission_id(permission)
    developer_role = role_id("developer")
    if context.is_offline_mode():
        # This permission is newer than the frozen foundation seed data.
        op.bulk_insert(permissions, [permission_row(permission)])
        op.bulk_insert(
            role_permissions,
            [{"role_id": developer_role, "permission_id": permission_value}],
        )
        op.bulk_insert(
            role_scopes,
            [
                {
                    "role_id": developer_role,
                    "permission_id": permission_value,
                    "scope_type": permission_scope(permission),
                }
            ],
        )
        return
    if bind.execute(
        sa.select(permissions.c.id).where(permissions.c.id == permission_value)
    ).first() is None:
        op.bulk_insert(permissions, [permission_row(permission)])

    if bind.execute(
        sa.select(role_permissions.c.permission_id).where(
            role_permissions.c.role_id == developer_role,
            role_permissions.c.permission_id == permission_value,
        )
    ).first() is None:
        op.bulk_insert(
            role_permissions,
            [{"role_id": developer_role, "permission_id": permission_value}],
        )
    if bind.execute(
        sa.select(role_scopes.c.permission_id).where(
            role_scopes.c.role_id == developer_role,
            role_scopes.c.permission_id == permission_value,
        )
    ).first() is None:
        op.bulk_insert(
            role_scopes,
            [
                {
                    "role_id": developer_role,
                    "permission_id": permission_value,
                    "scope_type": permission_scope(permission),
                }
            ],
        )


def downgrade() -> None:
    permission_value = permission_id(Permission.SYSTEM_RUNTIME_RESET)
    developer_role = role_id("developer")
    op.execute(
        sa.text(
            "DELETE FROM nlp_role_permission_scopes "
            "WHERE role_id = :role_id AND permission_id = :permission_id"
        ).bindparams(role_id=developer_role, permission_id=permission_value)
    )
    op.execute(
        sa.text(
            "DELETE FROM nlp_role_permissions "
            "WHERE role_id = :role_id AND permission_id = :permission_id"
        ).bindparams(role_id=developer_role, permission_id=permission_value)
    )
    op.execute(
        sa.text("DELETE FROM nlp_permissions WHERE id = :permission_id").bindparams(
            permission_id=permission_value
        )
    )
