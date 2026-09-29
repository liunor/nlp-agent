"""add release notes table and manage permission

Revision ID: 20260813_16
Revises: 20260805_15
Create Date: 2026-08-13 00:00:00
"""

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from migrations.rbac_seed_data import Permission
from migrations.rbac_seed_data import permission_id, permission_row, permission_scope, role_id


revision = "20260813_16"
down_revision = "20260805_15"
branch_labels = None
depends_on = None


RELEASE_NOTES_PERMISSION = Permission.SYSTEM_RELEASE_NOTES_MANAGE


def upgrade() -> None:
    op.create_table(
        "nlp_release_notes",
        sa.Column("id", sa.String(length=36, collation="ascii_bin"), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("released_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("notes_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="published", nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version", name="uq_nlp_release_notes_version"),
        sa.Index("ix_nlp_release_notes_status_released_at", "status", "released_at"),
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
    role_permission_scopes_table = sa.table(
        "nlp_role_permission_scopes",
        sa.column("role_id", sa.String()),
        sa.column("permission_id", sa.String()),
        sa.column("scope_type", sa.String()),
    )

    developer_role_id = role_id("developer")
    manage_permission_id = permission_id(RELEASE_NOTES_PERMISSION)
    scope_type = permission_scope(RELEASE_NOTES_PERMISSION)

    # The RBAC foundation migration (20260804_12) seeds every Permission enum
    # member, so a freshly-created database already contains this permission and
    # its developer grant/scope. Seed only what is missing so this migration is
    # idempotent across fresh databases (CI) and databases migrated from the
    # pre-release-notes enum. In offline mode there is no live bind to query, so
    # fall back to plain inserts as the rendered SQL is not auto-executed.
    if context.is_offline_mode():
        op.bulk_insert(permissions_table, [permission_row(RELEASE_NOTES_PERMISSION)])
        op.bulk_insert(
            role_permissions_table,
            [{"role_id": developer_role_id, "permission_id": manage_permission_id}],
        )
        op.bulk_insert(
            role_permission_scopes_table,
            [{"role_id": developer_role_id, "permission_id": manage_permission_id, "scope_type": scope_type}],
        )
        return

    bind = op.get_bind()

    permission_exists = bind.execute(
        sa.select(permissions_table.c.id).where(permissions_table.c.id == manage_permission_id)
    ).first() is not None
    if not permission_exists:
        op.bulk_insert(permissions_table, [permission_row(RELEASE_NOTES_PERMISSION)])

    grant_exists = bind.execute(
        sa.select(role_permissions_table.c.permission_id).where(
            role_permissions_table.c.role_id == developer_role_id,
            role_permissions_table.c.permission_id == manage_permission_id,
        )
    ).first() is not None
    if not grant_exists:
        op.bulk_insert(
            role_permissions_table,
            [{"role_id": developer_role_id, "permission_id": manage_permission_id}],
        )

    scope_exists = bind.execute(
        sa.select(role_permission_scopes_table.c.permission_id).where(
            role_permission_scopes_table.c.role_id == developer_role_id,
            role_permission_scopes_table.c.permission_id == manage_permission_id,
            role_permission_scopes_table.c.scope_type == scope_type,
        )
    ).first() is not None
    if not scope_exists:
        op.bulk_insert(
            role_permission_scopes_table,
            [{"role_id": developer_role_id, "permission_id": manage_permission_id, "scope_type": scope_type}],
        )


def downgrade() -> None:
    # Reverse the release-notes table and its developer-scoped manage grant,
    # matching the order seeded in upgrade().
    manage_permission_id = permission_id(RELEASE_NOTES_PERMISSION)
    developer_role_id = role_id("developer")
    op.execute(
        sa.text(
            "DELETE FROM nlp_role_permission_scopes "
            "WHERE role_id = :role_id AND permission_id = :permission_id"
        ).bindparams(role_id=developer_role_id, permission_id=manage_permission_id)
    )
    op.execute(
        sa.text(
            "DELETE FROM nlp_role_permissions "
            "WHERE role_id = :role_id AND permission_id = :permission_id"
        ).bindparams(role_id=developer_role_id, permission_id=manage_permission_id)
    )
    op.execute(
        sa.text("DELETE FROM nlp_permissions WHERE id = :permission_id").bindparams(
            permission_id=manage_permission_id
        )
    )
    op.drop_table("nlp_release_notes")
