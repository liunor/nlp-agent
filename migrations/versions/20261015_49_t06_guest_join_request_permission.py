"""grant guests their own classroom enrollment request capability"""

from alembic import op
import sqlalchemy as sa

from core.rbac import Permission
from server.rbac.catalog import permission_id, permission_scope, role_id


revision = "20261015_49_t06"
down_revision = "20261015_48_t06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    permission = Permission.CLASSROOM_JOIN_REQUEST_SUBMIT
    op.execute(
        sa.text(
            "INSERT IGNORE INTO nlp_permissions "
            "(id, code, domain_name, resource_name, action_name, name, description, status, is_builtin) "
            "VALUES (:id, :code, :domain, :resource, :action, :name, :description, 'active', 1)"
        ).bindparams(
            id=permission_id(permission),
            code=permission.value,
            domain="classroom",
            resource="join_request",
            action="submit",
            name=permission.value,
            description="Create and read the current user's classroom join requests",
        )
    )
    for role_code in ("guest", "student", "teacher", "developer"):
        op.execute(
            sa.text(
                "INSERT IGNORE INTO nlp_role_permissions(role_id, permission_id) "
                "VALUES (:role_id, :permission_id)"
            ).bindparams(
                role_id=role_id(role_code), permission_id=permission_id(permission)
            )
        )
        op.execute(
            sa.text(
                "INSERT IGNORE INTO nlp_role_permission_scopes "
                "(role_id, permission_id, scope_type) "
                "VALUES (:role_id, :permission_id, :scope_type)"
            ).bindparams(
                role_id=role_id(role_code),
                permission_id=permission_id(permission),
                scope_type=permission_scope(permission),
            )
        )


def downgrade() -> None:
    permission = Permission.CLASSROOM_JOIN_REQUEST_SUBMIT
    for role_code in ("guest", "student", "teacher", "developer"):
        op.execute(
            sa.text(
                "DELETE FROM nlp_role_permission_scopes "
                "WHERE role_id = :role_id AND permission_id = :permission_id"
            ).bindparams(
                role_id=role_id(role_code), permission_id=permission_id(permission)
            )
        )
        op.execute(
            sa.text(
                "DELETE FROM nlp_role_permissions "
                "WHERE role_id = :role_id AND permission_id = :permission_id"
            ).bindparams(
                role_id=role_id(role_code), permission_id=permission_id(permission)
            )
        )
    op.execute(
        sa.text("DELETE FROM nlp_permissions WHERE id = :permission_id").bindparams(
            permission_id=permission_id(permission)
        )
    )
