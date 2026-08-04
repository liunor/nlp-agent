"""Initial migration - create identity and workspace tables.

Revision ID: 001_initial
Revises: None
Create Date: 2026-08-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Users table
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("email", sa.String(254), nullable=True, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "disabled", "locked", "deleted", name="user_status"),
            nullable=False,
            server_default="active",
        ),
        sa.Column("token_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_users_email", "users", ["email"])

    # Roles table
    op.create_table(
        "roles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column(
            "scope",
            sa.Enum("system", "workspace", name="role_scope"),
            nullable=False,
        ),
    )

    # User roles association
    op.create_table(
        "user_roles",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role_id",
            sa.String(36),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    # Auth sessions table
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_secret_hash", sa.String(64), nullable=False),
        sa.Column("user_token_version", sa.Integer, nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(64), nullable=True),
        sa.Column("ip_prefix", sa.LargeBinary(16), nullable=True),
        sa.Column("user_agent_hash", sa.LargeBinary(32), nullable=True),
    )
    op.create_index(
        "ix_auth_sessions_active",
        "auth_sessions",
        ["user_id", "revoked_at", "expires_at"],
    )

    # Workspaces table
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "type",
            sa.Enum("personal", "organization", "classroom", name="workspace_type"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "suspended", "deleted", name="workspace_status"),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_by_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Workspace members
    op.create_table(
        "workspace_members",
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role_id",
            sa.String(36),
            sa.ForeignKey("roles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("active", "invited", "removed", name="member_status"),
            nullable=False,
            server_default="active",
        ),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_workspace_members_user",
        "workspace_members",
        ["user_id", "status"],
    )

    # Agent sessions
    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "archived", "deleted", name="session_status"),
            nullable=False,
            server_default="active",
        ),
        sa.Column("active_turn_id", sa.String(36), nullable=True),
        sa.Column("model_profile_id", sa.String(36), nullable=True),
        sa.Column("metadata", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_agent_sessions_workspace_updated",
        "agent_sessions",
        ["workspace_id", "status", "updated_at"],
    )
    op.create_index(
        "ix_agent_sessions_creator",
        "agent_sessions",
        ["created_by_user_id", "created_at"],
    )

    # Turns
    op.create_table(
        "turns",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("agent_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "submitted_by_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "accepted",
                "queued",
                "running",
                "cancelling",
                "completed",
                "cancelled",
                "failed",
                "interrupted",
                name="turn_state",
            ),
            nullable=False,
            server_default="accepted",
        ),
        sa.Column("input_payload", sa.JSON, nullable=False),
        sa.Column("output_summary", sa.JSON, nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uk_turn_idempotency",
        "turns",
        ["workspace_id", "session_id", "idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_turns_session_state",
        "turns",
        ["session_id", "state", "created_at"],
    )
    op.create_index(
        "ix_turns_workspace_created",
        "turns",
        ["workspace_id", "created_at"],
    )

    # Turn events
    op.create_table(
        "turn_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "turn_id",
            sa.String(36),
            sa.ForeignKey("turns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.BigInteger, nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uk_turn_event_sequence",
        "turn_events",
        ["turn_id", "sequence"],
        unique=True,
    )
    op.create_index(
        "uk_turn_event_idempotency",
        "turn_events",
        ["turn_id", "idempotency_key"],
        unique=True,
    )

    # Agent checkpoints
    op.create_table(
        "agent_checkpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("agent_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "turn_id",
            sa.String(36),
            sa.ForeignKey("turns.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("checkpoint_no", sa.BigInteger, nullable=False),
        sa.Column("state_version", sa.String(32), nullable=False),
        sa.Column("state_blob", sa.LargeBinary, nullable=False),
        sa.Column("state_sha256", sa.LargeBinary(32), nullable=False),
        sa.Column("encrypted_data_key", sa.LargeBinary(512), nullable=True),
        sa.Column(
            "status",
            sa.Enum("ready", "superseded", "purged", name="checkpoint_status"),
            nullable=False,
            server_default="ready",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uk_checkpoint_number",
        "agent_checkpoints",
        ["session_id", "checkpoint_no"],
        unique=True,
    )

    # Execution leases
    op.create_table(
        "execution_leases",
        sa.Column(
            "turn_id",
            sa.String(36),
            sa.ForeignKey("turns.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("lease_token", sa.LargeBinary(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="1"),
    )
    op.create_index("uk_lease_token", "execution_leases", ["lease_token"], unique=True)

    # Outbox messages
    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(36), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_outbox_unpublished",
        "outbox_messages",
        ["published_at", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_table("outbox_messages")
    op.drop_table("execution_leases")
    op.drop_table("agent_checkpoints")
    op.drop_table("turn_events")
    op.drop_table("turns")
    op.drop_table("agent_sessions")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
    op.drop_table("auth_sessions")
    op.drop_table("user_roles")
    op.drop_table("roles")
    op.drop_table("users")
