"""add class join request (user management, chained after V3 16)

Reuses the existing ``nlp_classrooms`` table as the class entity (V3 already has
it via 20260804_14_classrooms_menus). This migration only adds the join-request
approval flow and does NOT create a second standalone class system.

Revision ID: 20260817_17
Revises: 20260813_16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "20260817_17"
down_revision = "20260813_16"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
    ]


def upgrade() -> None:
    op.create_table(
        "nlp_class_join_requests",
        sa.Column("id", sa.String(36, collation="ascii_bin"), primary_key=True),
        sa.Column("class_id", sa.String(36, collation="ascii_bin"), nullable=False, comment="关联 nlp_classrooms.id"),
        sa.Column("user_id", sa.String(36, collation="ascii_bin"), nullable=False, comment="申请人"),
        sa.Column("student_number", sa.String(32), nullable=True, comment="学生选填学号"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("requested_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Column("reviewed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("reviewed_by", sa.String(36, collation="ascii_bin"), nullable=True),
        sa.ForeignKeyConstraint(["class_id"], ["nlp_classrooms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["nlp_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["nlp_users.id"], ondelete="SET NULL"),
        sa.Index("ix_nlp_class_join_requests_class_status", "class_id", "status"),
        # 防重复申请：同一 (班级, 用户, 状态) 只能有一条；即同一个 pending 不会被重复提交
        sa.UniqueConstraint("class_id", "user_id", "status", name="uq_nlp_class_join_requests_cls_usr_sts"),
        *_timestamps(),
    )
    # Apply the human-readable table comment immediately so the table matches
    # ``TABLE_COMMENTS["nlp_class_join_requests"]`` on a fresh database. The
    # earlier ``20260815_17_table_comments`` bulk-ALTER intentionally skips
    # tables that do not yet exist at its chain position, so each newly added
    # table is responsible for setting its own comment here or via a
    # dedicated ``*_table_comments`` follow-up (see 20260817_18).
    op.execute(
        "ALTER TABLE `nlp_class_join_requests` "
        "COMMENT = '课堂加入申请与审批流转（用户管理）。'"
    )


def downgrade() -> None:
    op.drop_table("nlp_class_join_requests")
