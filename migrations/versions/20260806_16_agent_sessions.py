"""add agent sessions and execution leases tables

Revision ID: 20260806_16
Revises: 20260805_15
Create Date: 2026-08-06 16:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "20260806_16"
down_revision = "20260805_15"
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
    # Agent Sessions table — higher-level orchestration entity that groups
    # gateway-managed conversations within a workspace context.
    op.create_table(
        "nlp_agent_sessions",
        sa.Column("id", sa.String(length=36, collation="ascii_bin"), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(length=36, collation="ascii_bin"),
            sa.ForeignKey("nlp_workspaces.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_by_user_id",
            sa.String(length=36, collation="ascii_bin"),
            sa.ForeignKey("nlp_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("active_conversation_id", sa.String(length=128, collation="ascii_bin"), nullable=True),
        sa.Column("model_profile_id", sa.String(length=36, collation="ascii_bin"), nullable=True),
        sa.Column("metadata", mysql.JSON, nullable=True),
        sa.Column("deleted_at", mysql.DATETIME(fsp=6), nullable=True),
        *_timestamps(),
        sa.Index("ix_agent_sessions_workspace_updated", "workspace_id", "status", "updated_at"),
        sa.Index("ix_agent_sessions_creator", "created_by_user_id", "created_at"),
    )

    # Execution Leases table — tracks which worker holds the execution
    # right for a turn. References the existing nlp_turns table.
    op.create_table(
        "nlp_execution_leases",
        sa.Column(
            "turn_id",
            sa.String(length=128, collation="ascii_bin"),
            sa.ForeignKey("nlp_turns.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("lease_token", sa.VARBINARY(32), nullable=False),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("heartbeat_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="1"),
        sa.UniqueConstraint("lease_token", name="uk_lease_token"),
        sa.Index("ix_leases_expiry", "expires_at"),
    )


def downgrade() -> None:
    op.drop_table("nlp_execution_leases")
    op.drop_table("nlp_agent_sessions")
