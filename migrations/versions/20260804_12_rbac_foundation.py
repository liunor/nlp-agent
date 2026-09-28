"""add normalized RBAC and workspace membership tables

Revision ID: 20260804_12
Revises: 20260802_11
Create Date: 2026-08-04 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from migrations.rbac_seed_data import Permission
from migrations.rbac_seed_data import (
    ROLE_NAMES,
    permission_row,
    role_permission_rows,
    role_permission_scope_rows,
    role_row,
)


revision = "20260804_12"
down_revision = "20260802_11"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("UTC_TIMESTAMP(6)"),
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("UTC_TIMESTAMP(6)"),
        ),
    ]


def upgrade() -> None:
    op.add_column(
        "nlp_users",
        sa.Column("authorization_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_table(
        "nlp_roles",
        sa.Column("id", sa.String(length=36, collation="ascii_bin"), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False, unique=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        *_timestamps(),
    )
    op.create_table(
        "nlp_permissions",
        sa.Column("id", sa.String(length=36, collation="ascii_bin"), primary_key=True),
        sa.Column("code", sa.String(length=128), nullable=False, unique=True),
        sa.Column("domain_name", sa.String(length=64), nullable=False),
        sa.Column("resource_name", sa.String(length=64), nullable=False),
        sa.Column("action_name", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        *_timestamps(),
        sa.UniqueConstraint("domain_name", "resource_name", "action_name", name="uq_nlp_permissions_triplet"),
    )
    op.create_table(
        "nlp_user_roles",
        sa.Column("user_id", sa.String(length=36, collation="ascii_bin"), nullable=False),
        sa.Column("role_id", sa.String(length=36, collation="ascii_bin"), nullable=False),
        sa.Column("assigned_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("assigned_by_user_id", sa.String(length=36, collation="ascii_bin"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["nlp_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["nlp_roles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_by_user_id"], ["nlp_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("user_id", "role_id"),
    )
    op.create_index("ix_nlp_user_roles_effective", "nlp_user_roles", ["user_id", "expires_at"])
    op.create_index("ix_nlp_user_roles_role", "nlp_user_roles", ["role_id"])
    op.create_table(
        "nlp_role_permissions",
        sa.Column("role_id", sa.String(length=36, collation="ascii_bin"), nullable=False),
        sa.Column("permission_id", sa.String(length=36, collation="ascii_bin"), nullable=False),
        sa.Column("granted_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Column("granted_by_user_id", sa.String(length=36, collation="ascii_bin"), nullable=True),
        sa.ForeignKeyConstraint(["role_id"], ["nlp_roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["permission_id"], ["nlp_permissions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["nlp_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("role_id", "permission_id"),
    )
    op.create_index("ix_nlp_role_permissions_permission", "nlp_role_permissions", ["permission_id"])
    op.create_table(
        "nlp_role_permission_scopes",
        sa.Column("role_id", sa.String(length=36, collation="ascii_bin"), nullable=False),
        sa.Column("permission_id", sa.String(length=36, collation="ascii_bin"), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["nlp_roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["permission_id"], ["nlp_permissions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("role_id", "permission_id", "scope_type"),
    )
    op.create_table(
        "nlp_workspace_members",
        sa.Column("workspace_id", sa.String(length=36, collation="ascii_bin"), nullable=False),
        sa.Column("user_id", sa.String(length=36, collation="ascii_bin"), nullable=False),
        sa.Column("member_type", sa.String(length=32), nullable=False, server_default="member"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        *_timestamps(),
        sa.ForeignKeyConstraint(["workspace_id"], ["nlp_workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["nlp_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id", "user_id"),
    )
    op.create_index("ix_nlp_workspace_members_user_status", "nlp_workspace_members", ["user_id", "status"])

    roles_table = sa.table(
        "nlp_roles",
        sa.column("id", sa.String()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("status", sa.String()),
        sa.column("is_builtin", sa.Boolean()),
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
    op.bulk_insert(roles_table, [role_row(code) for code in ROLE_NAMES])
    op.bulk_insert(permissions_table, [permission_row(item) for item in Permission])
    op.bulk_insert(
        sa.table(
            "nlp_role_permissions",
            sa.column("role_id", sa.String()),
            sa.column("permission_id", sa.String()),
        ),
        role_permission_rows(),
    )
    op.bulk_insert(
        sa.table(
            "nlp_role_permission_scopes",
            sa.column("role_id", sa.String()),
            sa.column("permission_id", sa.String()),
            sa.column("scope_type", sa.String()),
        ),
        role_permission_scope_rows(),
    )


def downgrade() -> None:
    op.drop_table("nlp_workspace_members")
    op.drop_table("nlp_role_permission_scopes")
    op.drop_table("nlp_role_permissions")
    op.drop_table("nlp_user_roles")
    op.drop_table("nlp_permissions")
    op.drop_table("nlp_roles")
    op.drop_column("nlp_users", "authorization_version")
