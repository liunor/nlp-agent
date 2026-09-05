"""remove the obsolete administrator menu-management entry.

The runtime visible-menu projection remains in place for navigation filtering;
only the unused management page and its menu entry are removed.
"""

from alembic import op
import sqlalchemy as sa

from migrations.rbac_seed_data import menu_id


revision = "20260830_38_remove_menu"
down_revision = "20260830_37_rbac_fixed_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    menu = menu_id("developer.menus")
    op.execute(sa.text("DELETE FROM nlp_role_menus WHERE menu_id = :menu_id").bindparams(menu_id=menu))
    op.execute(sa.text("DELETE FROM nlp_menus WHERE id = :menu_id").bindparams(menu_id=menu))


def downgrade() -> None:
    # The management UI and APIs were intentionally removed, so restoring the
    # old menu entry would create a dead navigation target.
    pass
