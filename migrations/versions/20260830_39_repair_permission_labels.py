"""repair persisted RBAC permission display labels.

Some environments already recorded the label migration while retaining
mis-decoded display text.  Reapply the stable catalog labels without changing
permission codes or role assignments.
"""

from alembic import op
import sqlalchemy as sa

from migrations.rbac_seed_data import Permission
from migrations.rbac_seed_data import PERMISSION_LABELS


revision = "20260830_39_fix_perm_labels"
down_revision = "20260830_38_remove_menu"
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
