"""add identity:account:delete_self permission for self-service account deletion

Grants every fixed role (via the shared ``guest`` base) the ability to
permanently delete their own account and data, which powers the "注销账号"
entry in the profile dialog.  The permission is seeded here — not in the frozen
``rbac_seed_data`` module — so older migrations keep replaying unchanged.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261001_48_account_delete_self"
down_revision = "20260929_47_merge_wb_transcript"
branch_labels = None
depends_on = None


PERMISSION_ID = "5ea1f76d-33c9-5ef2-ba54-d1736f29c570"
PERMISSION_CODE = "identity:account:delete_self"
GUEST_ROLE_ID = "5029c91b-c8bc-5770-baa5-537bc6bd28b6"


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
    op.bulk_insert(
        permissions,
        [
            {
                "id": PERMISSION_ID,
                "code": PERMISSION_CODE,
                "domain_name": "identity",
                "resource_name": "account",
                "action_name": "delete_self",
                "name": "注销账号",
                "description": "永久删除当前账号及其全部个人数据，不可恢复。",
                "status": "active",
                "is_builtin": True,
            }
        ],
    )
    op.bulk_insert(
        sa.table(
            "nlp_role_permissions",
            sa.column("role_id", sa.String()),
            sa.column("permission_id", sa.String()),
        ),
        [{"role_id": GUEST_ROLE_ID, "permission_id": PERMISSION_ID}],
    )
    op.bulk_insert(
        sa.table(
            "nlp_role_permission_scopes",
            sa.column("role_id", sa.String()),
            sa.column("permission_id", sa.String()),
            sa.column("scope_type", sa.String()),
        ),
        [{"role_id": GUEST_ROLE_ID, "permission_id": PERMISSION_ID, "scope_type": "own"}],
    )


def downgrade() -> None:
    permissions = sa.table(
        "nlp_permissions",
        sa.column("id", sa.String()),
        sa.column("code", sa.String()),
    )
    role_permissions = sa.table(
        "nlp_role_permissions",
        sa.column("role_id", sa.String()),
        sa.column("permission_id", sa.String()),
    )
    role_permission_scopes = sa.table(
        "nlp_role_permission_scopes",
        sa.column("role_id", sa.String()),
        sa.column("permission_id", sa.String()),
    )
    op.execute(
        role_permission_scopes.delete().where(
            role_permission_scopes.c.permission_id == PERMISSION_ID
        )
    )
    op.execute(
        role_permissions.delete().where(
            role_permissions.c.permission_id == PERMISSION_ID
        )
    )
    op.execute(
        permissions.delete().where(permissions.c.code == PERMISSION_CODE)
    )
