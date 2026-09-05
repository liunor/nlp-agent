"""Remove the obsolete Agent session management menu projection."""

from alembic import op
import sqlalchemy as sa

from server.rbac.catalog import menu_id


revision = "20260903_46_remove_dev_sessions"
down_revision = "20260901_45_audit_quota_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    old_menu_id = menu_id("developer.sessions")
    op.execute(
        sa.text("DELETE FROM nlp_role_menus WHERE menu_id = :menu_id").bindparams(
            menu_id=old_menu_id
        )
    )
    op.execute(
        sa.text("DELETE FROM nlp_menus WHERE id = :menu_id").bindparams(
            menu_id=old_menu_id
        )
    )


def downgrade() -> None:
    # The menu was intentionally removed as a product capability. Restoring it
    # on downgrade would expose a page that no longer exists in the client.
    pass
