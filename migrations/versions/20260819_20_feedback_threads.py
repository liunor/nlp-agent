"""add persistent student feedback threads"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

from migrations.rbac_seed_data import permission_id, role_id
from migrations.rbac_seed_data import Permission


revision = "20260819_20"
down_revision = "20260818_19"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid_type = sa.String(36, collation="ascii_bin")
    timestamp_type = DATETIME(fsp=6)
    op.create_table("nlp_feedback_threads", sa.Column("id", uuid_type, nullable=False), sa.Column("user_id", uuid_type, nullable=False), sa.Column("developer_read_at", timestamp_type, nullable=True), sa.Column("created_at", timestamp_type, server_default=sa.func.utc_timestamp(6), nullable=False), sa.Column("updated_at", timestamp_type, server_default=sa.func.utc_timestamp(6), nullable=False), sa.ForeignKeyConstraint(["user_id"], ["nlp_users.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("user_id", name="uq_nlp_feedback_threads_user_id"))
    op.create_table("nlp_feedback_messages", sa.Column("id", uuid_type, nullable=False), sa.Column("thread_id", uuid_type, nullable=False), sa.Column("sender_user_id", uuid_type, nullable=False), sa.Column("sender_type", sa.String(16), nullable=False), sa.Column("body", sa.Text(), nullable=False), sa.Column("created_at", timestamp_type, server_default=sa.func.utc_timestamp(6), nullable=False), sa.Column("updated_at", timestamp_type, server_default=sa.func.utc_timestamp(6), nullable=False), sa.ForeignKeyConstraint(["sender_user_id"], ["nlp_users.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["thread_id"], ["nlp_feedback_threads.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"))
    op.execute("ALTER TABLE `nlp_feedback_threads` COMMENT = '学生意见反馈会话，按用户聚合反馈线程。'")
    op.execute("ALTER TABLE `nlp_feedback_messages` COMMENT = '反馈会话中的逐条消息与发送方类型。'")
    op.execute(sa.text("INSERT IGNORE INTO nlp_permissions(id, code, domain_name, resource_name, action_name, name, description, status, is_builtin) VALUES (:id, :code, :domain, :resource, :action, :name, '', 'active', 1)").bindparams(id=permission_id(Permission.LEARNING_FEEDBACK_SUBMIT), code=Permission.LEARNING_FEEDBACK_SUBMIT.value, domain="learning", resource="feedback", action="submit", name=Permission.LEARNING_FEEDBACK_SUBMIT.value))
    op.execute(sa.text("INSERT IGNORE INTO nlp_permissions(id, code, domain_name, resource_name, action_name, name, description, status, is_builtin) VALUES (:id, :code, :domain, :resource, :action, :name, '', 'active', 1)").bindparams(id=permission_id(Permission.LEARNING_FEEDBACK_READ), code=Permission.LEARNING_FEEDBACK_READ.value, domain="learning", resource="feedback", action="read", name=Permission.LEARNING_FEEDBACK_READ.value))
    for role_code in ("student", "teacher", "developer"):
        op.execute(sa.text("INSERT IGNORE INTO nlp_role_permissions(role_id, permission_id) VALUES (:role_id, :permission_id)").bindparams(role_id=role_id(role_code), permission_id=permission_id(Permission.LEARNING_FEEDBACK_SUBMIT)))
        op.execute(sa.text("INSERT IGNORE INTO nlp_role_permission_scopes(role_id, permission_id, scope_type) VALUES (:role_id, :permission_id, 'own')").bindparams(role_id=role_id(role_code), permission_id=permission_id(Permission.LEARNING_FEEDBACK_SUBMIT)))
    op.execute(sa.text("INSERT IGNORE INTO nlp_role_permissions(role_id, permission_id) VALUES (:role_id, :permission_id)").bindparams(role_id=role_id("developer"), permission_id=permission_id(Permission.LEARNING_FEEDBACK_READ)))
    op.execute(sa.text("INSERT IGNORE INTO nlp_role_permission_scopes(role_id, permission_id, scope_type) VALUES (:role_id, :permission_id, 'system')").bindparams(role_id=role_id("developer"), permission_id=permission_id(Permission.LEARNING_FEEDBACK_READ)))


def downgrade() -> None:
    for role_code in ("student", "teacher", "developer"):
        op.execute(sa.text("DELETE FROM nlp_role_permission_scopes WHERE role_id = :role_id AND permission_id = :permission_id").bindparams(role_id=role_id(role_code), permission_id=permission_id(Permission.LEARNING_FEEDBACK_SUBMIT)))
        op.execute(sa.text("DELETE FROM nlp_role_permissions WHERE role_id = :role_id AND permission_id = :permission_id").bindparams(role_id=role_id(role_code), permission_id=permission_id(Permission.LEARNING_FEEDBACK_SUBMIT)))
    op.execute(sa.text("DELETE FROM nlp_role_permission_scopes WHERE role_id = :role_id AND permission_id = :permission_id").bindparams(role_id=role_id("developer"), permission_id=permission_id(Permission.LEARNING_FEEDBACK_READ)))
    op.execute(sa.text("DELETE FROM nlp_role_permissions WHERE role_id = :role_id AND permission_id = :permission_id").bindparams(role_id=role_id("developer"), permission_id=permission_id(Permission.LEARNING_FEEDBACK_READ)))
    op.execute(sa.text("DELETE FROM nlp_permissions WHERE id = :permission_id").bindparams(permission_id=permission_id(Permission.LEARNING_FEEDBACK_SUBMIT)))
    op.execute(sa.text("DELETE FROM nlp_permissions WHERE id = :permission_id").bindparams(permission_id=permission_id(Permission.LEARNING_FEEDBACK_READ)))
    op.drop_table("nlp_feedback_messages")
    op.drop_table("nlp_feedback_threads")
