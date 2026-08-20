"""close the database authentication and execution ownership seams

Revision ID: 20260819_22
Revises: 20260818_21
Create Date: 2026-08-19 00:00:00
"""

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from core.rbac import Permission
from server.rbac.catalog import (
    menu_row,
    permission_id,
    permission_row,
    permission_scope,
    role_id,
    role_menu_rows,
    MENU_CATALOG,
)


revision = "20260819_22"
down_revision = "20260818_21"
branch_labels = None
depends_on = None

SENSITIVE_DATA_PERMISSION = Permission.SYSTEM_SENSITIVE_DATA_READ


def _column_names(table_name: str) -> set[str]:
    if context.is_offline_mode():
        return set()
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if context.is_offline_mode() or column.name not in _column_names(table_name):
        op.add_column(table_name, column)


def _index_names(table_name: str) -> set[str]:
    if context.is_offline_mode():
        return set()
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if context.is_offline_mode() or index_name not in _index_names(table_name):
        op.create_index(index_name, table_name, columns)


def _foreign_key_exists(table_name: str, columns: list[str], referred_table: str) -> bool:
    if context.is_offline_mode():
        return False
    return any(
        tuple(foreign_key["constrained_columns"]) == tuple(columns)
        and foreign_key["referred_table"] == referred_table
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table_name)
    )


def _create_foreign_key_if_missing(
    constraint_name: str,
    table_name: str,
    referred_table: str,
    columns: list[str],
    referred_columns: list[str],
) -> None:
    if context.is_offline_mode() or not _foreign_key_exists(table_name, columns, referred_table):
        op.create_foreign_key(
            constraint_name,
            table_name,
            referred_table,
            columns,
            referred_columns,
            ondelete="RESTRICT",
        )


def upgrade() -> None:
    _add_column_if_missing(
        "nlp_users",
        sa.Column("last_login_at", mysql.DATETIME(fsp=6), nullable=True),
    )
    _create_index_if_missing("ix_nlp_users_last_login_at", "nlp_users", ["last_login_at"])
    _add_column_if_missing(
        "nlp_sessions",
        sa.Column(
            "issued_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("UTC_TIMESTAMP(6)"),
        ),
    )
    _add_column_if_missing(
        "nlp_sessions",
        sa.Column("last_seen_at", mysql.DATETIME(fsp=6), nullable=True),
    )
    _add_column_if_missing(
        "nlp_sessions",
        sa.Column("authorization_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_table(
        "nlp_ws_tickets",
        sa.Column("id", sa.String(36, collation="ascii_bin"), primary_key=True),
        sa.Column("auth_session_id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("user_id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("workspace_id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("ticket_hash", sa.String(128), nullable=False),
        sa.Column("origin", sa.String(255), nullable=False),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("used_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.ForeignKeyConstraint(["auth_session_id"], ["nlp_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["nlp_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["nlp_workspaces.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("ticket_hash", name="uq_nlp_ws_tickets_ticket_hash"),
    )
    op.create_index(
        "ix_nlp_ws_tickets_session_expires",
        "nlp_ws_tickets",
        ["auth_session_id", "expires_at"],
    )
    op.create_index("ix_nlp_ws_tickets_user_id", "nlp_ws_tickets", ["user_id"])
    op.create_index("ix_nlp_ws_tickets_expires_at", "nlp_ws_tickets", ["expires_at"])

    # ConversationModel is imported by the early master-data migration.  On a
    # clean database that means ``channel`` already exists before this
    # lifecycle migration runs; on older databases it still needs to be added.
    _add_column_if_missing(
        "nlp_conversations",
        sa.Column("channel", sa.String(32), nullable=False, server_default="web"),
    )

    # Close the legacy gap for accounts created before V3: every active or
    # disabled account must have an explicit least-privilege role.
    op.execute(
        sa.text(
            """
            INSERT INTO nlp_user_roles (user_id, role_id)
            SELECT u.id, r.id
            FROM nlp_users AS u
            JOIN nlp_roles AS r ON r.code = 'guest' AND r.status = 'active'
            LEFT JOIN nlp_user_roles AS ur ON ur.user_id = u.id
            WHERE ur.user_id IS NULL
            """
        )
    )

    permissions_table = sa.table(
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
    role_permissions_table = sa.table(
        "nlp_role_permissions",
        sa.column("role_id", sa.String()),
        sa.column("permission_id", sa.String()),
    )
    scopes_table = sa.table(
        "nlp_role_permission_scopes",
        sa.column("role_id", sa.String()),
        sa.column("permission_id", sa.String()),
        sa.column("scope_type", sa.String()),
    )
    permission = permission_id(SENSITIVE_DATA_PERMISSION)
    developer = role_id("developer")
    scope = permission_scope(SENSITIVE_DATA_PERMISSION)
    if context.is_offline_mode():
        op.bulk_insert(permissions_table, [permission_row(SENSITIVE_DATA_PERMISSION)])
        op.bulk_insert(role_permissions_table, [{"role_id": developer, "permission_id": permission}])
        op.bulk_insert(scopes_table, [{"role_id": developer, "permission_id": permission, "scope_type": scope}])
    else:
        bind = op.get_bind()
        if bind.execute(sa.select(permissions_table.c.id).where(permissions_table.c.id == permission)).first() is None:
            op.bulk_insert(permissions_table, [permission_row(SENSITIVE_DATA_PERMISSION)])
        if bind.execute(sa.select(role_permissions_table.c.permission_id).where(role_permissions_table.c.role_id == developer, role_permissions_table.c.permission_id == permission)).first() is None:
            op.bulk_insert(role_permissions_table, [{"role_id": developer, "permission_id": permission}])
        if bind.execute(sa.select(scopes_table.c.permission_id).where(scopes_table.c.role_id == developer, scopes_table.c.permission_id == permission, scopes_table.c.scope_type == scope)).first() is None:
            op.bulk_insert(scopes_table, [{"role_id": developer, "permission_id": permission, "scope_type": scope}])

    # Seed the developer control-plane menu projection.  These records are
    # stable across environments, while custom roles can bind to them through
    # the role-menu API.  The frontend route bundle remains static; this table
    # is the authoritative visibility/binding projection.
    menus_table = sa.table(
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
    role_menus_table = sa.table(
        "nlp_role_menus",
        sa.column("role_id", sa.String()),
        sa.column("menu_id", sa.String()),
    )
    if context.is_offline_mode():
        op.bulk_insert(menus_table, [menu_row(item) for item in MENU_CATALOG])
        op.bulk_insert(role_menus_table, role_menu_rows())
    else:
        bind = op.get_bind()
        for item in MENU_CATALOG:
            row = menu_row(item)
            if bind.execute(sa.select(menus_table.c.id).where(menus_table.c.id == row["id"])).first() is None:
                op.bulk_insert(menus_table, [row])
        for row in role_menu_rows():
            exists = bind.execute(
                sa.select(role_menus_table.c.menu_id).where(
                    role_menus_table.c.role_id == row["role_id"],
                    role_menus_table.c.menu_id == row["menu_id"],
                )
            ).first()
            if exists is None:
                op.bulk_insert(role_menus_table, [row])

    # New execution/checkpoint rows are ownership-bound.  The columns remain
    # nullable for legacy rows so a rolling deployment can migrate safely; the
    # read/write services reject legacy unowned rows rather than guessing an
    # owner from a session id.
    for table in (
        "nlp_agent_checkpoints",
        "nlp_langgraph_checkpoints",
        "nlp_langgraph_checkpoint_blobs",
        "nlp_langgraph_checkpoint_writes",
    ):
        _add_column_if_missing(
            table,
            sa.Column("workspace_id", sa.String(36, collation="ascii_bin"), nullable=True),
        )
        _add_column_if_missing(
            table,
            sa.Column("owner_user_id", sa.String(36, collation="ascii_bin"), nullable=True),
        )
        _create_index_if_missing(f"ix_{table}_workspace_id", table, ["workspace_id"])
        _create_index_if_missing(f"ix_{table}_owner_user_id", table, ["owner_user_id"])
        _create_foreign_key_if_missing(
            f"fk_{table}_workspace_id",
            table,
            "nlp_workspaces",
            ["workspace_id"],
            ["id"],
        )
        _create_foreign_key_if_missing(
            f"fk_{table}_owner_user_id",
            table,
            "nlp_users",
            ["owner_user_id"],
            ["id"],
        )


def downgrade() -> None:
    permission = permission_id(SENSITIVE_DATA_PERMISSION)
    developer = role_id("developer")
    op.execute(sa.text("DELETE FROM nlp_role_permission_scopes WHERE role_id = :role_id AND permission_id = :permission_id").bindparams(role_id=developer, permission_id=permission))
    op.execute(sa.text("DELETE FROM nlp_role_permissions WHERE role_id = :role_id AND permission_id = :permission_id").bindparams(role_id=developer, permission_id=permission))
    op.execute(sa.text("DELETE FROM nlp_permissions WHERE id = :permission_id").bindparams(permission_id=permission))
    # ``channel`` may have been created by the earlier dynamic master-data
    # migration on a clean database, so preserve it during downgrade rather
    # than risking removal of a pre-existing column.
    for table in (
        "nlp_langgraph_checkpoint_writes",
        "nlp_langgraph_checkpoint_blobs",
        "nlp_langgraph_checkpoints",
        "nlp_agent_checkpoints",
    ):
        op.drop_constraint(f"fk_{table}_owner_user_id", table_name=table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_workspace_id", table_name=table, type_="foreignkey")
        op.drop_index(f"ix_{table}_owner_user_id", table_name=table)
        op.drop_index(f"ix_{table}_workspace_id", table_name=table)
        op.drop_column(table, "owner_user_id")
        op.drop_column(table, "workspace_id")
    op.drop_index("ix_nlp_ws_tickets_expires_at", table_name="nlp_ws_tickets")
    op.drop_index("ix_nlp_ws_tickets_user_id", table_name="nlp_ws_tickets")
    op.drop_index("ix_nlp_ws_tickets_session_expires", table_name="nlp_ws_tickets")
    op.drop_table("nlp_ws_tickets")
    op.drop_column("nlp_sessions", "authorization_version")
    op.drop_column("nlp_sessions", "last_seen_at")
    op.drop_column("nlp_sessions", "issued_at")
    op.drop_index("ix_nlp_users_last_login_at", table_name="nlp_users")
    op.drop_column("nlp_users", "last_login_at")
