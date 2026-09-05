"""Backfill the self-service quota permission for built-in roles.

The quota permission was added to the Python RBAC catalogue together with the
quota feature.  Existing databases may already have applied the original RBAC
foundation migration before that permission existed, so the catalogue alone
does not update their persisted role projections.
"""

from alembic import context, op
import sqlalchemy as sa

from core.rbac import Permission
from server.rbac.catalog import ROLE_NAMES, permission_id, permission_row, permission_scope, role_id


revision = "20260831_41_quota_self_usage"
down_revision = "20260831_40_quota_feedback_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fresh-install offline scripts already receive the permission from the
    # RBAC foundation's current catalogue.  The idempotent online path below
    # is for databases created before the quota permission was introduced.
    if context.is_offline_mode():
        return

    permission = Permission.QUOTA_USAGE_READ_SELF
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
    permission_row_value = permission_row(permission)
    if bind.execute(
        sa.select(permissions.c.id).where(permissions.c.id == permission_id(permission))
    ).first() is None:
        op.bulk_insert(permissions, [permission_row_value])

    permission_value = permission_id(permission)
    scope_value = permission_scope(permission)
    for role_code in ROLE_NAMES:
        builtin_role_id = role_id(role_code)
        if bind.execute(
            sa.select(role_permissions.c.permission_id).where(
                role_permissions.c.role_id == builtin_role_id,
                role_permissions.c.permission_id == permission_value,
            )
        ).first() is None:
            op.bulk_insert(
                role_permissions,
                [{"role_id": builtin_role_id, "permission_id": permission_value}],
            )
        if bind.execute(
            sa.select(role_scopes.c.permission_id).where(
                role_scopes.c.role_id == builtin_role_id,
                role_scopes.c.permission_id == permission_value,
                role_scopes.c.scope_type == scope_value,
            )
        ).first() is None:
            op.bulk_insert(
                role_scopes,
                [
                    {
                        "role_id": builtin_role_id,
                        "permission_id": permission_value,
                        "scope_type": scope_value,
                    }
                ],
            )


def downgrade() -> None:
    permission = Permission.QUOTA_USAGE_READ_SELF
    permission_value = permission_id(permission)
    for role_code in ROLE_NAMES:
        role_value = role_id(role_code)
        op.execute(
            sa.text(
                "DELETE FROM nlp_role_permission_scopes "
                "WHERE role_id = :role_id AND permission_id = :permission_id"
            ).bindparams(role_id=role_value, permission_id=permission_value)
        )
        op.execute(
            sa.text(
                "DELETE FROM nlp_role_permissions "
                "WHERE role_id = :role_id AND permission_id = :permission_id"
            ).bindparams(role_id=role_value, permission_id=permission_value)
        )
    op.execute(
        sa.text("DELETE FROM nlp_permissions WHERE id = :permission_id").bindparams(
            permission_id=permission_value
        )
    )
