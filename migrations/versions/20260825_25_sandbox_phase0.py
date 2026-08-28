"""sandbox identity and lease contracts (Phase 0).

Revision ID: 20260825_25
Revises: 20260820_24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import BIGINT, DATETIME


revision = "20260825_25"
down_revision = "20260820_24"
branch_labels = None
depends_on = None


UUID = sa.String(36, collation="ascii_bin")


def upgrade() -> None:
    op.create_table(
        "nlp_sandbox_environments",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_user_id", UUID, sa.ForeignKey("nlp_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("resource_profile_id", sa.String(64), nullable=False, server_default="python-base"),
        sa.Column("profile_revision", BIGINT(unsigned=True), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="ready"),
        sa.Column("generation", BIGINT(unsigned=True), nullable=False, server_default="1"),
        sa.Column("active_runtime_id", UUID, nullable=True),
        sa.Column("last_active_at", DATETIME(fsp=6), nullable=True),
        sa.Column("lease_deadline_at", DATETIME(fsp=6), nullable=True),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.UniqueConstraint("owner_user_id", name="uq_nlp_sandbox_environments_owner"),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_nlp_sandbox_environments_id_owner"),
        comment="每位用户唯一的逻辑沙箱归属与配置版本，不保存代码。",
    )
    op.create_index("ix_nlp_sandbox_environments_status_deadline", "nlp_sandbox_environments", ["status", "lease_deadline_at"])
    op.create_index("ix_nlp_sandbox_environments_active_runtime_id", "nlp_sandbox_environments", ["active_runtime_id"])

    op.create_table(
        "nlp_sandbox_runtime_instances",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("environment_id", UUID, sa.ForeignKey("nlp_sandbox_environments.id", ondelete="SET NULL")),
        sa.Column("node_id", sa.String(128)),
        sa.Column("runtime_kind", sa.String(32), nullable=False, server_default="unassigned"),
        sa.Column("external_runtime_id", sa.String(255)),
        sa.Column("image_digest", sa.String(255)),
        sa.Column("resource_profile_id", sa.String(64), nullable=False, server_default="python-base"),
        sa.Column("state", sa.String(16), nullable=False, server_default="declared"),
        sa.Column("generation", BIGINT(unsigned=True), nullable=False, server_default="1"),
        sa.Column("claim_nonce_hash", sa.String(128)),
        sa.Column("last_heartbeat_at", DATETIME(fsp=6)),
        sa.Column("failure_reason", sa.String(500)),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.UniqueConstraint("external_runtime_id", name="uq_nlp_sandbox_runtime_external_id"),
        comment="沙箱运行实例声明与健康状态；Phase 0 不创建容器。",
    )
    op.create_index("ix_nlp_sandbox_runtime_instances_environment_id", "nlp_sandbox_runtime_instances", ["environment_id"])
    op.create_index("ix_nlp_sandbox_runtime_state_profile", "nlp_sandbox_runtime_instances", ["state", "resource_profile_id"])

    op.create_table(
        "nlp_sandbox_leases",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("environment_id", UUID, sa.ForeignKey("nlp_sandbox_environments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("nlp_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("auth_session_id", UUID, sa.ForeignKey("nlp_sessions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("runtime_instance_id", UUID, sa.ForeignKey("nlp_sandbox_runtime_instances.id", ondelete="SET NULL")),
        sa.Column("workspace_id", UUID, sa.ForeignKey("nlp_workspaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False, server_default="browser"),
        sa.Column("generation", BIGINT(unsigned=True), nullable=False, server_default="1"),
        sa.Column("state", sa.String(16), nullable=False, server_default="active"),
        sa.Column("issued_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Column("renewed_at", DATETIME(fsp=6)),
        sa.Column("expires_at", DATETIME(fsp=6), nullable=False),
        sa.Column("released_at", DATETIME(fsp=6)),
        sa.Column("reason", sa.String(128)),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.UniqueConstraint("environment_id", "auth_session_id", name="uq_nlp_sandbox_leases_environment_session"),
        sa.ForeignKeyConstraint(
            ["environment_id", "user_id"],
            ["nlp_sandbox_environments.id", "nlp_sandbox_environments.owner_user_id"],
            name="fk_nlp_sandbox_leases_environment_owner",
        ),
        comment="认证会话绑定的沙箱租约、过期与撤销状态。",
    )
    op.create_index("ix_nlp_sandbox_leases_session_state_expiry", "nlp_sandbox_leases", ["auth_session_id", "state", "expires_at"])
    op.create_index("ix_nlp_sandbox_leases_user_state", "nlp_sandbox_leases", ["user_id", "state"])

    op.create_table(
        "nlp_sandbox_executions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("environment_id", UUID, sa.ForeignKey("nlp_sandbox_environments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("runtime_instance_id", UUID, sa.ForeignKey("nlp_sandbox_runtime_instances.id", ondelete="SET NULL")),
        sa.Column("lease_id", UUID, sa.ForeignKey("nlp_sandbox_leases.id", ondelete="SET NULL")),
        sa.Column("owner_user_id", UUID, sa.ForeignKey("nlp_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("workspace_id", UUID, sa.ForeignKey("nlp_workspaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False, unique=True),
        sa.Column("code_hash", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("generation", BIGINT(unsigned=True), nullable=False),
        sa.Column("started_at", DATETIME(fsp=6)),
        sa.Column("completed_at", DATETIME(fsp=6)),
        sa.Column("exit_reason", sa.String(128)),
        sa.Column("resource_summary_json", sa.JSON),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_nlp_sandbox_executions_id_owner"),
        sa.ForeignKeyConstraint(
            ["environment_id", "owner_user_id"],
            ["nlp_sandbox_environments.id", "nlp_sandbox_environments.owner_user_id"],
            name="fk_nlp_sandbox_executions_environment_owner",
        ),
        comment="沙箱执行的最小审计摘要，不保存代码或标准输出。",
    )
    op.create_index("ix_nlp_sandbox_executions_environment_created", "nlp_sandbox_executions", ["environment_id", "created_at"])

    op.create_table(
        "nlp_sandbox_artifacts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("execution_id", UUID, sa.ForeignKey("nlp_sandbox_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", UUID, sa.ForeignKey("nlp_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column("locator", sa.String(512), nullable=False),
        sa.Column("sha256", sa.String(64)),
        sa.Column("size_bytes", BIGINT(unsigned=True), nullable=False, server_default="0"),
        sa.Column("expires_at", DATETIME(fsp=6)),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.ForeignKeyConstraint(
            ["execution_id", "owner_user_id"],
            ["nlp_sandbox_executions.id", "nlp_sandbox_executions.owner_user_id"],
            name="fk_nlp_sandbox_artifacts_execution_owner",
        ),
        comment="沙箱产物的受控存储指针与过期信息。",
    )
    op.create_index("ix_nlp_sandbox_artifacts_execution", "nlp_sandbox_artifacts", ["execution_id"])


def downgrade() -> None:
    op.drop_table("nlp_sandbox_artifacts")
    op.drop_table("nlp_sandbox_executions")
    op.drop_table("nlp_sandbox_leases")
    op.drop_table("nlp_sandbox_runtime_instances")
    op.drop_table("nlp_sandbox_environments")
