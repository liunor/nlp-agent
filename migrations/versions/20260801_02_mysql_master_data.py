"""create teaching, learning, conversation, turn and event master data

Revision ID: 20260801_02
Revises: 20260801_01
Create Date: 2026-08-01 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from server.infrastructure.mysql.models import (
    BlueprintRubricModel, ConversationMessageModel, ConversationModel, CourseCatalogModel,
    CourseCatalogVersionModel, CourseTopicModel, ExerciseAttemptModel, ExerciseQuestionModel,
    ExerciseSessionModel, KnowledgePointModel, LearningEvidenceModel, TeachingBlueprintModel,
    TeachingGoalModel, TurnEventModel,
)


revision = "20260801_02"
down_revision = "20260801_01"
branch_labels = None
depends_on = None

# Do not import the live TurnModel: its T07 foreign key references a table
# created only in revision 52. Keep the historical Turn DDL independent.
TURN_TABLE = sa.Table(
    "nlp_turns", sa.MetaData(naming_convention=ConversationModel.metadata.naming_convention),
    sa.Column("id", sa.String(36, collation="ascii_bin"), primary_key=True),
    sa.Column("conversation_id", sa.String(128, collation="ascii_bin"),
              sa.ForeignKey(ConversationModel.__table__.c.id, ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("workspace_id", sa.String(36, collation="ascii_bin"), nullable=False, index=True),
    sa.Column("user_id", sa.String(36, collation="ascii_bin"), nullable=False),
    sa.Column("status", sa.String(16), nullable=False, server_default="accepted"),
    sa.Column("input_text", sa.Text(), nullable=False),
    sa.Column("result_text", sa.Text()),
    sa.Column("error_kind", sa.String(128)),
    sa.Column("error_message", sa.String(1000)),
    sa.Column("learning_state_json", sa.JSON()),
    sa.Column("idempotency_key", sa.String(128)),
    sa.Column("claim_generation", mysql.BIGINT(unsigned=True), nullable=False, server_default="0"),
    sa.Column("claimed_by", sa.String(128)),
    sa.Column("lease_expires_at", mysql.DATETIME(fsp=6)),
    sa.Column("heartbeat_at", mysql.DATETIME(fsp=6)),
    sa.Column("started_at", mysql.DATETIME(fsp=6)),
    sa.Column("completed_at", mysql.DATETIME(fsp=6)),
    sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("(UTC_TIMESTAMP(6))")),
    sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("(UTC_TIMESTAMP(6))")),
    sa.UniqueConstraint("user_id", "conversation_id", "idempotency_key", name="uq_nlp_turns_user_conversation_idempotency"),
    comment="对话执行轮次的输入输出、状态与处理租约。",
)

TABLES = (
    TeachingGoalModel.__table__, CourseCatalogModel.__table__, CourseTopicModel.__table__,
    KnowledgePointModel.__table__, TeachingBlueprintModel.__table__, BlueprintRubricModel.__table__,
    CourseCatalogVersionModel.__table__, ConversationModel.__table__, TURN_TABLE,
    ConversationMessageModel.__table__, TurnEventModel.__table__, ExerciseSessionModel.__table__,
    ExerciseQuestionModel.__table__, ExerciseAttemptModel.__table__, LearningEvidenceModel.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        table.create(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(TABLES):
        table.drop(bind=bind)
