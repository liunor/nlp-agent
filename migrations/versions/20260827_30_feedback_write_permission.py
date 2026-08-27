"""grant developer feedback write permission"""

from alembic import op
import sqlalchemy as sa

from core.rbac import Permission
from server.rbac.catalog import permission_id, permission_scope, role_id

revision = "20260827_30"
down_revision = "20260827_29"
branch_labels = None
depends_on = None


def upgrade() -> None:
    permission = Permission.LEARNING_FEEDBACK_WRITE
    op.execute(
        sa.text(
            "INSERT IGNORE INTO nlp_permissions"
            "(id, code, domain_name, resource_name, action_name, name, description, status, is_builtin) "
            "VALUES (:id, :code, :domain, :resource, :action, :name, '', 'active', 1)"
        ).bindparams(
            id=permission_id(permission),
            code=permission.value,
            domain="learning",
            resource="feedback",
            action="write",
            name=permission.value,
        )
    )
    op.execute(
        sa.text(
            "INSERT IGNORE INTO nlp_role_permissions(role_id, permission_id) "
            "VALUES (:role_id, :permission_id)"
        ).bindparams(role_id=role_id("developer"), permission_id=permission_id(permission))
    )
    op.execute(
        sa.text(
            "INSERT IGNORE INTO nlp_role_permission_scopes(role_id, permission_id, scope_type) "
            "VALUES (:role_id, :permission_id, :scope_type)"
        ).bindparams(
            role_id=role_id("developer"),
            permission_id=permission_id(permission),
            scope_type=permission_scope(permission),
        )
    )


def downgrade() -> None:
    permission = Permission.LEARNING_FEEDBACK_WRITE
    op.execute(
        sa.text(
            "DELETE FROM nlp_role_permission_scopes "
            "WHERE role_id = :role_id AND permission_id = :permission_id"
        ).bindparams(role_id=role_id("developer"), permission_id=permission_id(permission))
    )
    op.execute(
        sa.text(
            "DELETE FROM nlp_role_permissions "
            "WHERE role_id = :role_id AND permission_id = :permission_id"
        ).bindparams(role_id=role_id("developer"), permission_id=permission_id(permission))
    )
    op.execute(
        sa.text("DELETE FROM nlp_permissions WHERE id = :permission_id").bindparams(
            permission_id=permission_id(permission)
        )
    )
