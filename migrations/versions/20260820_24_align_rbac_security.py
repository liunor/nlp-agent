"""Keep the built-in developer role least-privileged by default."""

from alembic import op
import sqlalchemy as sa

from migrations.rbac_seed_data import Permission
from migrations.rbac_seed_data import permission_id, role_id


revision = "20260820_24"
down_revision = "20260820_23"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The permission remains in the catalog for explicit, separately-audited
    # grants.  It must not be inherited by every developer account: developer
    # diagnostics are metadata-only unless sensitive-data access is deliberate.
    op.execute(
        sa.text(
            "DELETE FROM nlp_role_permission_scopes "
            "WHERE role_id = :role_id AND permission_id = :permission_id"
        ).bindparams(
            role_id=role_id("developer"),
            permission_id=permission_id(Permission.SYSTEM_SENSITIVE_DATA_READ),
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM nlp_role_permissions "
            "WHERE role_id = :role_id AND permission_id = :permission_id"
        ).bindparams(
            role_id=role_id("developer"),
            permission_id=permission_id(Permission.SYSTEM_SENSITIVE_DATA_READ),
        )
    )


def downgrade() -> None:
    # Restore the grant that 20260819_22 introduced when rolling back this
    # alignment migration.  INSERT IGNORE keeps the downgrade idempotent for
    # databases where an operator restored the grant manually.
    role = role_id("developer")
    permission = permission_id(Permission.SYSTEM_SENSITIVE_DATA_READ)
    op.execute(
        sa.text(
            "INSERT IGNORE INTO nlp_role_permissions (role_id, permission_id) "
            "VALUES (:role_id, :permission_id)"
        ).bindparams(role_id=role, permission_id=permission)
    )
    op.execute(
        sa.text(
            "INSERT IGNORE INTO nlp_role_permission_scopes "
            "(role_id, permission_id, scope_type) "
            "VALUES (:role_id, :permission_id, 'system')"
        ).bindparams(role_id=role, permission_id=permission)
    )
