"""backfill readable descriptions for the four fixed RBAC roles."""

from alembic import op
import sqlalchemy as sa

from migrations.rbac_seed_data import ROLE_DESCRIPTIONS


revision = "20260830_40_role_descriptions"
down_revision = "20260830_39_fix_perm_labels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    roles = sa.table(
        "nlp_roles",
        sa.column("code", sa.String()),
        sa.column("description", sa.String()),
    )
    for code, description in ROLE_DESCRIPTIONS.items():
        op.execute(
            roles.update()
            .where(roles.c.code == code)
            .values(description=description)
        )


def downgrade() -> None:
    roles = sa.table(
        "nlp_roles",
        sa.column("code", sa.String()),
        sa.column("description", sa.String()),
    )
    for code in ROLE_DESCRIPTIONS:
        op.execute(
            roles.update()
            .where(roles.c.code == code)
            .values(description="")
        )
