"""add class join request system (user-management v2 missing feature)

Revision ID: 20260812_17
Revises: 20260806_16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "20260812_17"
down_revision = "20260806_16"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
    ]


def upgrade() -> None:
    op.create_table(
        "nlp_classes",
        sa.Column("id", sa.String(36, collation="ascii_bin"), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, comment="班级名称"),
        sa.Column("grade", sa.String(32), nullable=True, comment="届别"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(36, collation="ascii_bin"), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["created_by"], ["nlp_users.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "nlp_class_enrollments",
        sa.Column("id", sa.String(36, collation="ascii_bin"), primary_key=True),
        sa.Column("class_id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("user_id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("student_number", sa.String(32), nullable=True, comment="学号"),
        sa.Column("major", sa.String(64), nullable=True, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        *_timestamps(),
        sa.ForeignKeyConstraint(["class_id"], ["nlp_classes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["nlp_users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("class_id", "user_id", name="uq_nlp_class_enrollments_class_user"),
    )
    op.create_table(
        "nlp_class_teachers",
        sa.Column("id", sa.String(36, collation="ascii_bin"), primary_key=True),
        sa.Column("class_id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("teacher_id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("subject", sa.String(32), nullable=True, comment="科目"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        *_timestamps(),
        sa.ForeignKeyConstraint(["class_id"], ["nlp_classes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["teacher_id"], ["nlp_users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("class_id", "teacher_id", name="uq_nlp_class_teachers_unique"),
    )
    op.create_table(
        "nlp_class_join_requests",
        sa.Column("id", sa.String(36, collation="ascii_bin"), primary_key=True),
        sa.Column("class_id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("user_id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("student_number", sa.String(32), nullable=True, comment="学生选填学号"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("requested_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.Column("reviewed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("reviewed_by", sa.String(36, collation="ascii_bin"), nullable=True),
        sa.ForeignKeyConstraint(["class_id"], ["nlp_classes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["nlp_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["nlp_users.id"], ondelete="SET NULL"),
        sa.Index("ix_nlp_class_join_requests_class_status", "class_id", "status"),
    )


def downgrade() -> None:
    op.drop_table("nlp_class_join_requests")
    op.drop_table("nlp_class_teachers")
    op.drop_table("nlp_class_enrollments")
    op.drop_table("nlp_classes")
