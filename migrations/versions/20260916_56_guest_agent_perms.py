"""Backfill basic Agent permissions for the built-in guest role."""

from __future__ import annotations

import json

from alembic import context, op
import sqlalchemy as sa

from core.rbac import Permission
from server.rbac.catalog import permission_id, permission_row, permission_scope, role_id


revision = "20260916_56_guest_agent_perms"
down_revision = "20260914_55_email_registration"
branch_labels = None
depends_on = None


GUEST_AGENT_PERMISSIONS = (
    Permission.AGENT_SESSION_CREATE,
    Permission.AGENT_SESSION_READ,
    Permission.AGENT_SESSION_UPDATE,
    Permission.AGENT_SESSION_DELETE,
    Permission.AGENT_TURN_SUBMIT,
    Permission.AGENT_TURN_CANCEL,
    Permission.AGENT_EVENT_REPLAY,
)

BACKUP_TABLE_NAME = "nlp_rbac_guest_agent_perm_backups"


def _backup_table() -> sa.TableClause:
    return sa.table(
        BACKUP_TABLE_NAME,
        sa.column("permission_id", sa.String()),
        sa.column("permission_existed", sa.Boolean()),
        sa.column("grant_existed", sa.Boolean()),
        sa.column("previous_scopes", sa.Text()),
    )


def _ensure_backup_table(bind: sa.Connection | None) -> None:
    if context.is_offline_mode():
        op.create_table(
            BACKUP_TABLE_NAME,
            sa.Column("permission_id", sa.String(length=36), primary_key=True),
            sa.Column("permission_existed", sa.Boolean(), nullable=False),
            sa.Column("grant_existed", sa.Boolean(), nullable=False),
            sa.Column("previous_scopes", sa.Text(), nullable=False),
        )
        return
    assert bind is not None
    if BACKUP_TABLE_NAME not in sa.inspect(bind).get_table_names():
        op.create_table(
            BACKUP_TABLE_NAME,
            sa.Column("permission_id", sa.String(length=36), primary_key=True),
            sa.Column("permission_existed", sa.Boolean(), nullable=False),
            sa.Column("grant_existed", sa.Boolean(), nullable=False),
            sa.Column("previous_scopes", sa.Text(), nullable=False),
        )


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
    role_permissions = sa.table(
        "nlp_role_permissions",
        sa.column("role_id", sa.String()),
        sa.column("permission_id", sa.String()),
    )
    role_scopes = sa.table(
        "nlp_role_permission_scopes",
        sa.column("role_id", sa.String()),
        sa.column("permission_id", sa.String()),
        sa.column("scope_type", sa.String()),
    )
    guest_role_id = role_id("guest")
    bind = None if context.is_offline_mode() else op.get_bind()
    _ensure_backup_table(bind)
    backups = _backup_table()

    # Existing databases were seeded before guest accounts received basic
    # Agent capabilities. Keep this online and idempotent so an operator's
    # existing role projection is repaired without overwriting other grants.
    if context.is_offline_mode():
        # The foundation migration already seeds the current permission and
        # role catalogues in a fresh offline script. Preserve only this
        # migration's backup-table state; repeating grants would violate keys.
        op.bulk_insert(
            backups,
            [
                {
                    "permission_id": permission_id(permission),
                    "permission_existed": True,
                    "grant_existed": True,
                    "previous_scopes": json.dumps([permission_scope(permission)]),
                }
                for permission in GUEST_AGENT_PERMISSIONS
            ],
        )
        return

    assert bind is not None

    for permission in GUEST_AGENT_PERMISSIONS:
        permission_value = permission_id(permission)
        if bind.execute(
            sa.select(backups.c.permission_id).where(
                backups.c.permission_id == permission_value
            )
        ).first() is None:
            permission_exists = bind.execute(
                sa.select(permissions.c.id).where(permissions.c.id == permission_value)
            ).first() is not None
            grant_exists = bind.execute(
                sa.select(role_permissions.c.permission_id).where(
                    role_permissions.c.role_id == guest_role_id,
                    role_permissions.c.permission_id == permission_value,
                )
            ).first() is not None
            previous_scopes = list(
                bind.scalars(
                    sa.select(role_scopes.c.scope_type).where(
                        role_scopes.c.role_id == guest_role_id,
                        role_scopes.c.permission_id == permission_value,
                    )
                )
            )
            op.bulk_insert(
                backups,
                [
                    {
                        "permission_id": permission_value,
                        "permission_existed": permission_exists,
                        "grant_existed": grant_exists,
                        "previous_scopes": json.dumps(previous_scopes),
                    }
                ],
            )

        if bind.execute(
            sa.select(permissions.c.id).where(permissions.c.id == permission_value)
        ).first() is None:
            op.bulk_insert(permissions, [permission_row(permission)])

        if bind.execute(
            sa.select(role_permissions.c.permission_id).where(
                role_permissions.c.role_id == guest_role_id,
                role_permissions.c.permission_id == permission_value,
            )
        ).first() is None:
            op.bulk_insert(
                role_permissions,
                [{"role_id": guest_role_id, "permission_id": permission_value}],
            )

        scope_value = permission_scope(permission)
        existing_scopes = set(
            bind.scalars(
                sa.select(role_scopes.c.scope_type).where(
                    role_scopes.c.role_id == guest_role_id,
                    role_scopes.c.permission_id == permission_value,
                )
            )
        )
        for existing_scope in existing_scopes - {scope_value}:
            op.execute(
                sa.delete(role_scopes).where(
                    role_scopes.c.role_id == guest_role_id,
                    role_scopes.c.permission_id == permission_value,
                    role_scopes.c.scope_type == existing_scope,
                )
            )
        if scope_value not in existing_scopes:
            op.bulk_insert(
                role_scopes,
                [
                    {
                        "role_id": guest_role_id,
                        "permission_id": permission_value,
                        "scope_type": scope_value,
                    }
                ],
            )


def downgrade() -> None:
    role_permissions = sa.table(
        "nlp_role_permissions",
        sa.column("role_id", sa.String()),
        sa.column("permission_id", sa.String()),
    )
    role_scopes = sa.table(
        "nlp_role_permission_scopes",
        sa.column("role_id", sa.String()),
        sa.column("permission_id", sa.String()),
        sa.column("scope_type", sa.String()),
    )
    guest_role_id = role_id("guest")

    if context.is_offline_mode():
        # Offline downgrade cannot inspect the backup rows. Remove only the
        # canonical guest projection and leave shared catalogue rows protected
        # by their other role references.
        for permission in GUEST_AGENT_PERMISSIONS:
            permission_value = permission_id(permission)
            op.execute(
                sa.delete(role_scopes).where(
                    role_scopes.c.role_id == guest_role_id,
                    role_scopes.c.permission_id == permission_value,
                )
            )
            op.execute(
                sa.delete(role_permissions).where(
                    role_permissions.c.role_id == guest_role_id,
                    role_permissions.c.permission_id == permission_value,
                )
            )
        op.drop_table(BACKUP_TABLE_NAME)
        return

    bind = op.get_bind()
    backups = _backup_table()
    if BACKUP_TABLE_NAME not in sa.inspect(bind).get_table_names():
        return

    for backup in bind.execute(sa.select(backups)).mappings():
        permission_value = backup["permission_id"]
        op.execute(
            sa.delete(role_scopes).where(
                role_scopes.c.role_id == guest_role_id,
                role_scopes.c.permission_id == permission_value,
            )
        )
        previous_scopes_value = backup["previous_scopes"] or "[]"
        previous_scopes = (
            json.loads(previous_scopes_value)
            if isinstance(previous_scopes_value, str)
            else previous_scopes_value
        )
        if previous_scopes:
            op.bulk_insert(
                role_scopes,
                [
                    {
                        "role_id": guest_role_id,
                        "permission_id": permission_value,
                        "scope_type": scope,
                    }
                    for scope in previous_scopes
                ],
            )
        if not backup["grant_existed"]:
            op.execute(
                sa.delete(role_permissions).where(
                    role_permissions.c.role_id == guest_role_id,
                    role_permissions.c.permission_id == permission_value,
                )
            )
        if not backup["permission_existed"]:
            op.execute(
                sa.text(
                    "DELETE FROM nlp_permissions "
                    "WHERE id = :permission_id "
                    "AND NOT EXISTS ("
                    "SELECT 1 FROM nlp_role_permissions "
                    "WHERE permission_id = :permission_id"
                    ")"
                ).bindparams(permission_id=permission_value)
            )
    op.drop_table(BACKUP_TABLE_NAME)
