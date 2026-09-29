"""Remove the obsolete developer sandbox menu projection."""

from alembic import op
import sqlalchemy as sa


revision = "20260916_57_rbac_menu_cleanup"
down_revision = "20260916_56_guest_agent_perms"
branch_labels = None
depends_on = None


SANDBOX_MENU_ID = "e9aaed99-d16c-5723-afeb-09a9487767d2"
DEVELOPER_ROLE_ID = "ddff0b44-2694-540f-a808-6f4d1827e413"
RUNTIME_MONITOR_PERMISSION_ID = "48e619da-1753-5a9a-9c11-810edd8d662c"


def upgrade() -> None:
    op.execute(
        sa.text("DELETE FROM nlp_role_menus WHERE menu_id = :menu_id").bindparams(
            menu_id=SANDBOX_MENU_ID
        )
    )
    op.execute(
        sa.text("DELETE FROM nlp_menus WHERE id = :menu_id").bindparams(
            menu_id=SANDBOX_MENU_ID
        )
    )


def downgrade() -> None:
    menus = sa.table(
        "nlp_menus",
        sa.column("id", sa.String()),
        sa.column("parent_id", sa.String()),
        sa.column("menu_type", sa.String()),
        sa.column("name", sa.String()),
        sa.column("route_path", sa.String()),
        sa.column("component_key", sa.String()),
        sa.column("permission_id", sa.String()),
        sa.column("client_scope", sa.String()),
        sa.column("sort_order", sa.Integer()),
        sa.column("visible", sa.Boolean()),
        sa.column("status", sa.String()),
    )
    role_menus = sa.table(
        "nlp_role_menus",
        sa.column("role_id", sa.String()),
        sa.column("menu_id", sa.String()),
    )
    op.bulk_insert(
        menus,
        [
            {
                "id": SANDBOX_MENU_ID,
                "parent_id": None,
                "menu_type": "page",
                "name": "代码沙箱",
                "route_path": "/developer/sandbox",
                "component_key": "sandbox",
                "permission_id": RUNTIME_MONITOR_PERMISSION_ID,
                "client_scope": "developer",
                "sort_order": 95,
                "visible": True,
                "status": "active",
            }
        ],
    )
    op.bulk_insert(
        role_menus,
        [{"role_id": DEVELOPER_ROLE_ID, "menu_id": SANDBOX_MENU_ID}],
    )
