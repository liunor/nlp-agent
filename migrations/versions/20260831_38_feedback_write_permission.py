"""grant developer permission for feedback workflow actions"""

from alembic import op
import sqlalchemy as sa
from uuid import NAMESPACE_URL, uuid5


PERMISSION_CODE = "learning:feedback:write"
PERMISSION_ID = str(uuid5(NAMESPACE_URL, f"pro-nlp/rbac/permission/{PERMISSION_CODE}"))
DEVELOPER_ROLE_ID = str(uuid5(NAMESPACE_URL, "pro-nlp/rbac/role/developer"))
PERMISSION_SCOPE = "system"

revision = "20260831_38_feedback_write"
down_revision = "20260831_37_feedback_meta"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT IGNORE INTO nlp_permissions "
            "(id, code, domain_name, resource_name, action_name, name, description, status, is_builtin) "
            "VALUES (:id, :code, :domain, :resource, :action, :name, '', 'active', 1)"
        ).bindparams(
            id=PERMISSION_ID,
            code=PERMISSION_CODE,
            domain="learning",
            resource="feedback",
            action="write",
            name=PERMISSION_CODE,
        )
    )
    op.execute(
        sa.text(
            "INSERT IGNORE INTO nlp_role_permissions(role_id, permission_id) "
            "VALUES (:role_id, :permission_id)"
        ).bindparams(
            role_id=DEVELOPER_ROLE_ID,
            permission_id=PERMISSION_ID,
        )
    )
    op.execute(
        sa.text(
            "INSERT IGNORE INTO nlp_role_permission_scopes(role_id, permission_id, scope_type) "
            "VALUES (:role_id, :permission_id, :scope_type)"
        ).bindparams(
            role_id=DEVELOPER_ROLE_ID,
            permission_id=PERMISSION_ID,
            scope_type=PERMISSION_SCOPE,
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM nlp_role_permission_scopes "
            "WHERE role_id = :role_id AND permission_id = :permission_id"
        ).bindparams(role_id=DEVELOPER_ROLE_ID, permission_id=PERMISSION_ID)
    )
    op.execute(
        sa.text(
            "DELETE FROM nlp_role_permissions "
            "WHERE role_id = :role_id AND permission_id = :permission_id"
        ).bindparams(role_id=DEVELOPER_ROLE_ID, permission_id=PERMISSION_ID)
    )
    op.execute(
        sa.text("DELETE FROM nlp_permissions WHERE id = :permission_id").bindparams(
            permission_id=PERMISSION_ID
        )
    )
