"""Minimal identity and session tables required by the MySQL foundation."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Computed,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.mysql import BIGINT, DATETIME
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampedModel
from .table_comments import TABLE_COMMENTS


UUID = String(36, collation="ascii_bin")
SESSION_IDENTIFIER = String(128, collation="ascii_bin")


class UserModel(TimestampedModel, Base):
    __tablename__ = "nlp_users"
    __table_args__ = (
        # §4.3 / 阶段4：用户名大小写归一化唯一约束。``username_lower`` 是 STORED
        # 生成列（见下方定义），保证 "Alice" 与 "alice" 在数据库层视为重复，
        # 杜绝同户名大小写不同的重复账号。
        Index("uq_nlp_users_username_lower", "username_lower", unique=True),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # 大小写归一化的持久化副本，由数据库自动计算（GENERATED ALWAYS AS (LOWER(username)) STORED）
    username_lower: Mapped[str] = mapped_column(
        String(64), Computed("LOWER(username)", persisted=True), nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    authorization_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    # P1-2 / 阶段5：软删生命周期。标记删除时间而非硬删除，保留学习历史与外键级联。
    deleted_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6), nullable=True, index=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6), nullable=True, index=True
    )

    sessions: Mapped[list["SessionModel"]] = relationship(back_populates="user")


class RoleModel(TimestampedModel, Base):
    __tablename__ = "nlp_roles"

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    is_builtin: Mapped[bool] = mapped_column(nullable=False, server_default="1")


class PermissionModel(TimestampedModel, Base):
    __tablename__ = "nlp_permissions"
    __table_args__ = (
        UniqueConstraint("domain_name", "resource_name", "action_name", name="uq_nlp_permissions_triplet"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    code: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    domain_name: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_name: Mapped[str] = mapped_column(String(64), nullable=False)
    action_name: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    is_builtin: Mapped[bool] = mapped_column(nullable=False, server_default="1")


class UserRoleModel(Base):
    __tablename__ = "nlp_user_roles"

    user_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_roles.id", ondelete="RESTRICT"), primary_key=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=func.utc_timestamp(6), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), index=True)
    assigned_by_user_id: Mapped[str | None] = mapped_column(
        UUID, ForeignKey("nlp_users.id", ondelete="SET NULL")
    )


class RolePermissionModel(Base):
    __tablename__ = "nlp_role_permissions"

    role_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_permissions.id", ondelete="RESTRICT"), primary_key=True
    )
    granted_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=func.utc_timestamp(6), nullable=False
    )
    granted_by_user_id: Mapped[str | None] = mapped_column(
        UUID, ForeignKey("nlp_users.id", ondelete="SET NULL")
    )


class RolePermissionScopeModel(Base):
    __tablename__ = "nlp_role_permission_scopes"

    role_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_permissions.id", ondelete="RESTRICT"), primary_key=True
    )
    scope_type: Mapped[str] = mapped_column(String(32), primary_key=True)


class WorkspaceMemberModel(TimestampedModel, Base):
    __tablename__ = "nlp_workspace_members"
    __table_args__ = (Index("ix_nlp_workspace_members_user_status", "user_id", "status"),)

    workspace_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_users.id", ondelete="CASCADE"), primary_key=True
    )
    member_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="member")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")


class AuthorizationAuditLogModel(Base):
    """Append-only authorization decisions and RBAC administration evidence."""

    __tablename__ = "nlp_authorization_audit_logs"
    __table_args__ = (
        Index("ix_nlp_authorization_audit_actor_created", "actor_user_id", "created_at"),
        Index("ix_nlp_authorization_audit_target_created", "target_user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    actor_user_id: Mapped[str | None] = mapped_column(
        UUID, ForeignKey("nlp_users.id", ondelete="SET NULL"), index=True
    )
    target_user_id: Mapped[str | None] = mapped_column(
        UUID, ForeignKey("nlp_users.id", ondelete="SET NULL"), index=True
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    permission_code: Mapped[str | None] = mapped_column(String(128))
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(128))
    detail_json: Mapped[dict] = mapped_column(JSON, nullable=False, server_default="('{}')")
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=func.utc_timestamp(6), nullable=False
    )


class ClassroomModel(TimestampedModel, Base):
    __tablename__ = "nlp_classrooms"

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_workspaces.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")


class ClassroomMemberModel(TimestampedModel, Base):
    __tablename__ = "nlp_classroom_members"
    __table_args__ = (
        # §4.3 / 阶段4：按 (班级, 状态, 角色) 列活跃成员 / 计数
        Index("ix_nlp_classroom_members_class_status_role", "classroom_id", "status", "member_role"),
    )

    classroom_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_classrooms.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_users.id", ondelete="CASCADE"), primary_key=True
    )
    member_role: Mapped[str] = mapped_column(String(16), nullable=False, server_default="student")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")


class MenuModel(TimestampedModel, Base):
    __tablename__ = "nlp_menus"

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("nlp_menus.id", ondelete="CASCADE"))
    menu_type: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    route_path: Mapped[str | None] = mapped_column(String(255))
    component_key: Mapped[str | None] = mapped_column(String(128))
    permission_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("nlp_permissions.id", ondelete="SET NULL"))
    client_scope: Mapped[str | None] = mapped_column(String(16))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    visible: Mapped[bool] = mapped_column(nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")


class RoleMenuModel(Base):
    __tablename__ = "nlp_role_menus"
    role_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_roles.id", ondelete="CASCADE"), primary_key=True)
    menu_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_menus.id", ondelete="CASCADE"), primary_key=True)


class WorkspaceModel(TimestampedModel, Base):
    __tablename__ = "nlp_workspaces"

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")


class SessionModel(TimestampedModel, Base):
    __tablename__ = "nlp_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_nlp_sessions_token_hash"),
        # §4.3 / 阶段4：撤销 / 清理查询（按用户列出有效 / 已撤销会话、按过期时间清扫）
        Index("ix_nlp_sessions_user_revoked_expires", "user_id", "revoked_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    user_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_users.id", ondelete="CASCADE"), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_workspaces.id", ondelete="RESTRICT"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=func.utc_timestamp(6), nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    authorization_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    expires_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)

    user: Mapped[UserModel] = relationship(back_populates="sessions")


class WsTicketModel(Base):
    __tablename__ = "nlp_ws_tickets"
    __table_args__ = (
        UniqueConstraint("ticket_hash", name="uq_nlp_ws_tickets_ticket_hash"),
        Index("ix_nlp_ws_tickets_session_expires", "auth_session_id", "expires_at"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    auth_session_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_sessions.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    ticket_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    origin: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)


class TeachingGoalModel(TimestampedModel, Base):
    __tablename__ = "nlp_teaching_goals"

    workspace_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_workspaces.id", ondelete="RESTRICT"), primary_key=True)
    goal_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, server_default="0")


class CourseCatalogModel(TimestampedModel, Base):
    __tablename__ = "nlp_course_catalogs"

    workspace_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_workspaces.id", ondelete="RESTRICT"), primary_key=True)
    revision: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, server_default="0")
    published_revision: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))


class CourseTopicModel(TimestampedModel, Base):
    __tablename__ = "nlp_course_topics"
    __table_args__ = (UniqueConstraint("workspace_id", "id", name="uq_nlp_course_topics_workspace_id_id"),)

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_course_catalogs.workspace_id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="enabled")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class KnowledgePointModel(TimestampedModel, Base):
    __tablename__ = "nlp_knowledge_points"
    __table_args__ = (UniqueConstraint("workspace_id", "id", name="uq_nlp_knowledge_points_workspace_id_id"),)

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_course_catalogs.workspace_id", ondelete="CASCADE"), nullable=False)
    topic_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_course_topics.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="enabled")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class TeachingBlueprintModel(TimestampedModel, Base):
    __tablename__ = "nlp_teaching_blueprints"
    __table_args__ = (Index("ix_nlp_blueprints_assignment", "workspace_id", "kind", "topic_id", "knowledge_point_id", "status"),)

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_course_catalogs.workspace_id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    topic_id: Mapped[str] = mapped_column(UUID, nullable=False)
    knowledge_point_id: Mapped[str | None] = mapped_column(UUID)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="draft")
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, server_default="0")


class BlueprintRubricModel(Base):
    __tablename__ = "nlp_blueprint_rubrics"
    __table_args__ = (UniqueConstraint("blueprint_id", "sort_order", name="uq_nlp_blueprint_rubrics_blueprint_id_sort_order"),)

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    blueprint_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_teaching_blueprints.id", ondelete="CASCADE"), nullable=False)
    criterion: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)


class CourseCatalogVersionModel(Base):
    __tablename__ = "nlp_course_catalog_versions"
    __table_args__ = (UniqueConstraint("workspace_id", "revision", name="uq_nlp_course_catalog_versions_workspace_id_revision"),)

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_course_catalogs.workspace_id", ondelete="CASCADE"), nullable=False)
    revision: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    change_summary: Mapped[str] = mapped_column(String(500), nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=func.utc_timestamp(6), nullable=False)


class ConversationModel(TimestampedModel, Base):
    __tablename__ = "nlp_conversations"
    id: Mapped[str] = mapped_column(SESSION_IDENTIFIER, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_workspaces.id", ondelete="RESTRICT"), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_users.id", ondelete="RESTRICT"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    channel: Mapped[str] = mapped_column(String(32), nullable=False, server_default="web")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    last_message_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))


class TurnModel(TimestampedModel, Base):
    __tablename__ = "nlp_turns"
    __table_args__ = (UniqueConstraint("user_id", "conversation_id", "idempotency_key", name="uq_nlp_turns_user_conversation_idempotency"),)
    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(SESSION_IDENTIFIER, ForeignKey("nlp_conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(UUID, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(UUID, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="accepted")
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    result_text: Mapped[str | None] = mapped_column(Text)
    error_kind: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(String(1000))
    learning_state_json: Mapped[dict | None] = mapped_column(JSON)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    claim_generation: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, server_default="0")
    claimed_by: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    started_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    completed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))


class ConversationMessageModel(Base):
    __tablename__ = "nlp_conversation_messages"
    __table_args__ = (UniqueConstraint("conversation_id", "sequence", name="uq_nlp_conversation_messages_conversation_id_sequence"),)
    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(SESSION_IDENTIFIER, ForeignKey("nlp_conversations.id", ondelete="CASCADE"), nullable=False)
    turn_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("nlp_turns.id", ondelete="SET NULL"))
    sequence: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=func.utc_timestamp(6), nullable=False)


class TurnEventModel(Base):
    __tablename__ = "nlp_turn_events"
    __table_args__ = (UniqueConstraint("turn_id", "sequence", name="uq_nlp_turn_events_turn_id_sequence"),)
    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    turn_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_turns.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    claim_generation: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, server_default="0")
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=func.utc_timestamp(6), nullable=False)


class ExerciseSessionModel(TimestampedModel, Base):
    __tablename__ = "nlp_exercise_sessions"
    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(SESSION_IDENTIFIER, ForeignKey("nlp_conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(UUID, nullable=False)
    user_id: Mapped[str] = mapped_column(UUID, nullable=False)
    topic_id: Mapped[str] = mapped_column(UUID, nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    blueprint_snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))


class ExerciseQuestionModel(Base):
    __tablename__ = "nlp_exercise_questions"
    __table_args__ = (UniqueConstraint("exercise_session_id", "sequence", name="uq_nlp_exercise_questions_session_sequence"),)
    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    exercise_session_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_exercise_sessions.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    rubric_json: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=func.utc_timestamp(6), nullable=False)


class ExerciseAttemptModel(Base):
    __tablename__ = "nlp_exercise_attempts"
    __table_args__ = (UniqueConstraint("exercise_question_id", "attempt_number", name="uq_nlp_exercise_attempts_question_attempt"),)
    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    exercise_question_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_exercise_questions.id", ondelete="CASCADE"), nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    rubric_matches_json: Mapped[list] = mapped_column(JSON, nullable=False)
    normalized_score: Mapped[int] = mapped_column(Integer, nullable=False)
    passed: Mapped[bool] = mapped_column(nullable=False)
    feedback: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=func.utc_timestamp(6), nullable=False)


class LearningEvidenceModel(Base):
    __tablename__ = "nlp_learning_evidence"
    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    exercise_session_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_exercise_sessions.id", ondelete="CASCADE"), nullable=False)
    exercise_question_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_exercise_questions.id", ondelete="CASCADE"), nullable=False)
    blueprint_snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    learner_answer: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_score: Mapped[int] = mapped_column(Integer, nullable=False)
    passed: Mapped[bool] = mapped_column(nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=func.utc_timestamp(6), nullable=False)


class GuidedSessionModel(TimestampedModel, Base):
    __tablename__ = "nlp_guided_sessions"
    __table_args__ = (Index("ix_nlp_guided_sessions_active", "conversation_id", "topic_id", "status"),)
    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(SESSION_IDENTIFIER, ForeignKey("nlp_conversations.id", ondelete="CASCADE"), nullable=False)
    workspace_id: Mapped[str] = mapped_column(UUID, nullable=False)
    user_id: Mapped[str] = mapped_column(UUID, nullable=False)
    topic_id: Mapped[str] = mapped_column(UUID, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    state_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    blueprint_snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))


class UserPreferenceModel(TimestampedModel, Base):
    __tablename__ = "nlp_user_preferences"
    user_id: Mapped[str] = mapped_column(UUID, primary_key=True)
    preferences_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, server_default="0")


class OutboxMessageModel(TimestampedModel, Base):
    __tablename__ = "nlp_outbox_messages"
    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    topic: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False, server_default=func.utc_timestamp(6))
    locked_by: Mapped[str | None] = mapped_column(String(128))
    locked_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    redis_message_id: Mapped[str | None] = mapped_column(String(128))
    published_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))


class TurnCancellationModel(Base):
    __tablename__ = "nlp_turn_cancellations"
    turn_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_turns.id", ondelete="CASCADE"), primary_key=True)
    requested_by: Mapped[str] = mapped_column(UUID, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False, server_default="")
    requested_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False, server_default=func.utc_timestamp(6))


class ToolCallModel(TimestampedModel, Base):
    __tablename__ = "nlp_tool_calls"
    __table_args__ = (UniqueConstraint("turn_id", "operation_id", name="uq_nlp_tool_calls_turn_id_operation_id"),)
    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    turn_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_turns.id", ondelete="CASCADE"), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    claim_generation: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    request_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    result_json: Mapped[dict | None] = mapped_column(JSON)


class DeadLetterModel(Base):
    __tablename__ = "nlp_dead_letters"
    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    turn_id: Mapped[str | None] = mapped_column(UUID)
    outbox_id: Mapped[str | None] = mapped_column(UUID)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    first_failed_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False, server_default=func.utc_timestamp(6))
    last_failed_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False, server_default=func.utc_timestamp(6))


class GatewayCompatModel(TimestampedModel, Base):
    """Historical schema declaration needed when Alembic replays revision 05.

    Runtime code must not use this retired projection; revision 20260802_11
    drops the table after its normalized replacements have been created.
    """

    __tablename__ = "nlp_gateway_compat"
    __table_args__ = (
        UniqueConstraint(
            "namespace",
            "aggregate_id",
            name="uq_nlp_gateway_compat_namespace_aggregate",
        ),
    )
    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    namespace: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default="0"
    )


class AgentCheckpointModel(TimestampedModel, Base):
    __tablename__ = "nlp_agent_checkpoints"
    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    workspace_id: Mapped[str | None] = mapped_column(
        UUID, ForeignKey("nlp_workspaces.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        UUID, ForeignKey("nlp_users.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    checkpoint_ns: Mapped[str] = mapped_column(String(128), nullable=False)
    checkpoint_id: Mapped[str] = mapped_column(String(128), nullable=False)
    checkpoint_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False)


class LangGraphCheckpointModel(Base):
    """Opaque LangGraph checkpoint envelope; payloads are serde-owned binary values."""
    __tablename__ = "nlp_langgraph_checkpoints"
    __table_args__ = (UniqueConstraint("thread_id", "checkpoint_ns", "checkpoint_id", name="uq_nlp_langgraph_checkpoint"),)

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    workspace_id: Mapped[str | None] = mapped_column(
        UUID, ForeignKey("nlp_workspaces.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        UUID, ForeignKey("nlp_users.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    checkpoint_ns: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    checkpoint_id: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(128))
    checkpoint_type: Mapped[str] = mapped_column(String(64), nullable=False)
    checkpoint_blob: Mapped[bytes] = mapped_column(LargeBinary(length=16_777_215), nullable=False)
    metadata_type: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_blob: Mapped[bytes] = mapped_column(LargeBinary(length=16_777_215), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=func.utc_timestamp(6), nullable=False)


class LangGraphCheckpointBlobModel(Base):
    __tablename__ = "nlp_langgraph_checkpoint_blobs"
    __table_args__ = (UniqueConstraint("thread_id", "checkpoint_ns", "channel", "version", name="uq_nlp_langgraph_checkpoint_blob"),)

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        UUID, ForeignKey("nlp_workspaces.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        UUID, ForeignKey("nlp_users.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    checkpoint_ns: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    channel: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    value_type: Mapped[str] = mapped_column(String(64), nullable=False)
    value_blob: Mapped[bytes] = mapped_column(LargeBinary(length=16_777_215), nullable=False)


class LangGraphCheckpointWriteModel(Base):
    __tablename__ = "nlp_langgraph_checkpoint_writes"
    __table_args__ = (UniqueConstraint("thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "write_index", name="uq_nlp_langgraph_checkpoint_write"),)

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(UUID, nullable=True, index=True)
    owner_user_id: Mapped[str | None] = mapped_column(UUID, nullable=True, index=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    checkpoint_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    write_index: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[str] = mapped_column(String(128), nullable=False)
    value_type: Mapped[str] = mapped_column(String(64), nullable=False)
    value_blob: Mapped[bytes] = mapped_column(LargeBinary(length=16_777_215), nullable=False)
    task_path: Mapped[str] = mapped_column(String(512), nullable=False, server_default="")


class ConversationTranscriptModel(Base):
    __tablename__ = "nlp_conversation_transcripts"
    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    message_uuid: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_uuid: Mapped[str | None] = mapped_column(String(128))
    message_type: Mapped[str] = mapped_column(String(32), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content_json: Mapped[dict | list | str] = mapped_column(JSON, nullable=False)
    tool_json: Mapped[dict | None] = mapped_column(JSON)
    usage_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=func.utc_timestamp(6), nullable=False)


class MemoryDocumentModel(TimestampedModel, Base):
    __tablename__ = "nlp_memory_documents"
    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    user_id: Mapped[str] = mapped_column(UUID, nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(UUID, nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    document_key: Mapped[str] = mapped_column(String(255), nullable=False)
    content_json: Mapped[dict | list | str] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, server_default="0")


class ReleaseNoteModel(TimestampedModel, Base):
    """Single-source versioned changelog edited by developers and read by students."""

    __tablename__ = "nlp_release_notes"
    __table_args__ = (
        UniqueConstraint("version", name="uq_nlp_release_notes_version"),
        Index("ix_nlp_release_notes_status_released_at", "status", "released_at"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    released_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    notes_json: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="published")


class MemoryArchiveModel(TimestampedModel, Base):
    __tablename__ = "nlp_memory_archives"
    __table_args__ = (UniqueConstraint("user_id", "workspace_id", "source_id", name="uq_nlp_memory_archive_source"),)
    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    user_id: Mapped[str] = mapped_column(UUID, nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(UUID, nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    cursor: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)


class MemoryCursorModel(Base):
    __tablename__ = "nlp_memory_cursors"
    scope_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    cursor: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, server_default="0")


class ToolAuditModel(Base):
    __tablename__ = "nlp_tool_audits"
    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    turn_id: Mapped[str | None] = mapped_column(UUID, index=True)
    operation_id: Mapped[str | None] = mapped_column(String(128), index=True)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(UUID)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    result_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=func.utc_timestamp(6), nullable=False)


class RuntimeConfigVersionModel(Base):
    __tablename__ = "nlp_runtime_config_versions"
    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=func.utc_timestamp(6), nullable=False)


class ObservabilityRecordModel(TimestampedModel, Base):
    """Append/upsert envelope store for traces, spans and telemetry events."""
    __tablename__ = "nlp_observability_records"
    __table_args__ = (UniqueConstraint("kind", "record_key", name="uq_nlp_observability_kind_record"),)

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    record_key: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(128), index=True)
    session_id: Mapped[str | None] = mapped_column(String(128), index=True)
    turn_id: Mapped[str | None] = mapped_column(String(128), index=True)
    status: Mapped[str | None] = mapped_column(String(32), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)


class ClassJoinRequestModel(TimestampedModel, Base):
    """Class join requests / approval flow (user management).

    Reuses the existing ``nlp_classrooms`` table as the class entity; only the
    approval request itself is modelled here.
    """

    __tablename__ = "nlp_class_join_requests"
    __table_args__ = (
        Index("ix_nlp_class_join_requests_class_status", "class_id", "status"),
        # 防重复申请：同一 (班级, 用户, 状态) 只能有一条 pending
        UniqueConstraint("class_id", "user_id", "status", name="uq_nlp_class_join_requests_cls_usr_sts"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    class_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_classrooms.id", ondelete="CASCADE"), nullable=False, comment="关联 nlp_classrooms.id"
    )
    user_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_users.id", ondelete="CASCADE"), nullable=False, comment="申请人"
    )
    student_number: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="学生选填学号")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending", comment="pending / approved / rejected"
    )
    requested_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=func.utc_timestamp(6))
    reviewed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(
        UUID, ForeignKey("nlp_users.id", ondelete="SET NULL")
    )

    cls: Mapped["ClassroomModel"] = relationship("ClassroomModel", foreign_keys=[class_id])
    user_: Mapped["UserModel"] = relationship("UserModel", foreign_keys=[user_id])
    reviewer: Mapped["UserModel | None"] = relationship("UserModel", foreign_keys=[reviewed_by])


class SandboxEnvironmentModel(TimestampedModel, Base):
    """Long-lived logical sandbox ownership; it never stores user code."""

    __tablename__ = "nlp_sandbox_environments"
    __table_args__ = (
        UniqueConstraint("owner_user_id", name="uq_nlp_sandbox_environments_owner"),
        UniqueConstraint("id", "owner_user_id", name="uq_nlp_sandbox_environments_id_owner"),
        Index("ix_nlp_sandbox_environments_status_deadline", "status", "lease_deadline_at"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_users.id", ondelete="RESTRICT"), nullable=False
    )
    resource_profile_id: Mapped[str] = mapped_column(String(64), nullable=False, server_default="python-base")
    profile_revision: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ready")
    generation: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, server_default="1")
    # Runtime rows are intentionally not represented by a hard foreign key here:
    # warm-pool runtimes can exist before they are claimed by an environment.
    active_runtime_id: Mapped[str | None] = mapped_column(UUID, nullable=True, index=True)
    last_active_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    lease_deadline_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)


class SandboxRuntimeInstanceModel(TimestampedModel, Base):
    """Runtime declaration only; Phase 0 does not create containers."""

    __tablename__ = "nlp_sandbox_runtime_instances"
    __table_args__ = (
        UniqueConstraint("external_runtime_id", name="uq_nlp_sandbox_runtime_external_id"),
        Index("ix_nlp_sandbox_runtime_state_profile", "state", "resource_profile_id"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    environment_id: Mapped[str | None] = mapped_column(
        UUID, ForeignKey("nlp_sandbox_environments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    runtime_kind: Mapped[str] = mapped_column(String(32), nullable=False, server_default="unassigned")
    external_runtime_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_digest: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resource_profile_id: Mapped[str] = mapped_column(String(64), nullable=False, server_default="python-base")
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="declared")
    generation: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, server_default="1")
    claim_nonce_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


class SandboxLeaseModel(TimestampedModel, Base):
    """A per-authentication-session grant to one user's environment."""

    __tablename__ = "nlp_sandbox_leases"
    __table_args__ = (
        UniqueConstraint("environment_id", "auth_session_id", name="uq_nlp_sandbox_leases_environment_session"),
        ForeignKeyConstraint(
            ["environment_id", "user_id"],
            ["nlp_sandbox_environments.id", "nlp_sandbox_environments.owner_user_id"],
            name="fk_nlp_sandbox_leases_environment_owner",
        ),
        Index("ix_nlp_sandbox_leases_session_state_expiry", "auth_session_id", "state", "expires_at"),
        Index("ix_nlp_sandbox_leases_user_state", "user_id", "state"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    environment_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_sandbox_environments.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_users.id", ondelete="RESTRICT"), nullable=False
    )
    auth_session_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    runtime_instance_id: Mapped[str | None] = mapped_column(
        UUID, ForeignKey("nlp_sandbox_runtime_instances.id", ondelete="SET NULL"), nullable=True
    )
    workspace_id: Mapped[str] = mapped_column(
        UUID, ForeignKey("nlp_workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default="browser")
    generation: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, server_default="1")
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    issued_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=func.utc_timestamp(6), nullable=False)
    renewed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(128), nullable=True)


class SandboxExecutionModel(Base):
    """Execution audit envelope; code and stdout are deliberately excluded."""

    __tablename__ = "nlp_sandbox_executions"
    __table_args__ = (
        UniqueConstraint("id", "owner_user_id", name="uq_nlp_sandbox_executions_id_owner"),
        ForeignKeyConstraint(
            ["environment_id", "owner_user_id"],
            ["nlp_sandbox_environments.id", "nlp_sandbox_environments.owner_user_id"],
            name="fk_nlp_sandbox_executions_environment_owner",
        ),
        Index("ix_nlp_sandbox_executions_environment_created", "environment_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    environment_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_sandbox_environments.id", ondelete="RESTRICT"), nullable=False)
    runtime_instance_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("nlp_sandbox_runtime_instances.id", ondelete="SET NULL"), nullable=True)
    lease_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("nlp_sandbox_leases.id", ondelete="SET NULL"), nullable=True)
    owner_user_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_users.id", ondelete="RESTRICT"), nullable=False)
    workspace_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_workspaces.id", ondelete="RESTRICT"), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    generation: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resource_summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=func.utc_timestamp(6), nullable=False)


class SandboxArtifactModel(Base):
    """Pointer-only artifact metadata; unsafe HTML is never trusted by the UI."""

    __tablename__ = "nlp_sandbox_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["execution_id", "owner_user_id"],
            ["nlp_sandbox_executions.id", "nlp_sandbox_executions.owner_user_id"],
            name="fk_nlp_sandbox_artifacts_execution_owner",
        ),
        Index("ix_nlp_sandbox_artifacts_execution", "execution_id"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    execution_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_sandbox_executions.id", ondelete="CASCADE"), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(UUID, ForeignKey("nlp_users.id", ondelete="RESTRICT"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    locator: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, server_default="0")
    expires_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), server_default=func.utc_timestamp(6), nullable=False)


for _table_name, _table_comment in TABLE_COMMENTS.items():
    Base.metadata.tables[_table_name].comment = _table_comment
