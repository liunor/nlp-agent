"""make RBAC permission labels readable in Chinese.

The permission codes remain stable API identifiers. This migration only
backfills the display name and description for rows created by older seeds.
"""

from alembic import op
import sqlalchemy as sa

from migrations.rbac_seed_data import Permission
from migrations.rbac_seed_data import PERMISSION_LABELS


revision = "20260830_37_rbac_fixed_roles"
down_revision = "20260829_36_usage_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    permissions = sa.table(
        "nlp_permissions",
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
    )
    for permission in Permission:
        name, description = PERMISSION_LABELS.get(permission, (permission.value, ""))
        op.execute(
            permissions.update()
            .where(permissions.c.code == permission.value)
            .values(name=name, description=description)
        )


def downgrade() -> None:
    permissions = sa.table(
        "nlp_permissions",
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
    )
    for permission in Permission:
        op.execute(
            permissions.update()
            .where(permissions.c.code == permission.value)
            .values(name=permission.value, description="")
        )
