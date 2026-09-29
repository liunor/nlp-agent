"""document the purpose of every MySQL table

Revision ID: 20260815_17
Revises: 20260813_16
Create Date: 2026-08-15 00:00:00
"""

from alembic import context, op

from server.infrastructure.mysql.table_comments import (
    SYSTEM_TABLE_COMMENTS,
    TABLE_COMMENTS,
)


revision = "20260815_17"
down_revision = "20260813_16"
branch_labels = None
depends_on = None


_TABLES_CREATED_AFTER = {"nlp_feedback_threads", "nlp_feedback_messages"}

# Keep the offline projection frozen at this revision.  The live table-comment
# registry grows with later migrations and cannot be inspected through
# Alembic's MockConnection while generating static SQL.
_TABLES_AVAILABLE_AT_REVISION = (
    "nlp_users",
    "nlp_roles",
    "nlp_permissions",
    "nlp_user_roles",
    "nlp_role_permissions",
    "nlp_role_permission_scopes",
    "nlp_workspace_members",
    "nlp_authorization_audit_logs",
    "nlp_classrooms",
    "nlp_classroom_members",
    "nlp_menus",
    "nlp_role_menus",
    "nlp_workspaces",
    "nlp_sessions",
    "nlp_teaching_goals",
    "nlp_course_catalogs",
    "nlp_course_topics",
    "nlp_knowledge_points",
    "nlp_teaching_blueprints",
    "nlp_blueprint_rubrics",
    "nlp_course_catalog_versions",
    "nlp_conversations",
    "nlp_turns",
    "nlp_conversation_messages",
    "nlp_turn_events",
    "nlp_exercise_sessions",
    "nlp_exercise_questions",
    "nlp_exercise_attempts",
    "nlp_learning_evidence",
    "nlp_guided_sessions",
    "nlp_user_preferences",
    "nlp_outbox_messages",
    "nlp_turn_cancellations",
    "nlp_tool_calls",
    "nlp_dead_letters",
    "nlp_agent_checkpoints",
    "nlp_langgraph_checkpoints",
    "nlp_langgraph_checkpoint_blobs",
    "nlp_langgraph_checkpoint_writes",
    "nlp_conversation_transcripts",
    "nlp_memory_documents",
    "nlp_release_notes",
    "nlp_memory_archives",
    "nlp_memory_cursors",
    "nlp_tool_audits",
    "nlp_runtime_config_versions",
    "nlp_observability_records",
    "alembic_version",
)

ALL_TABLE_COMMENTS = {
    table_name: table_comment
    for table_name, table_comment in {**TABLE_COMMENTS, **SYSTEM_TABLE_COMMENTS}.items()
    if table_name not in _TABLES_CREATED_AFTER
}


def _mysql_string_literal(value: str) -> str:
    """Return a safely quoted literal for the static migration comments."""

    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    if context.is_offline_mode():
        for table_name in _TABLES_AVAILABLE_AT_REVISION:
            op.execute(
                f"ALTER TABLE `{table_name}` COMMENT = "
                f"{_mysql_string_literal(ALL_TABLE_COMMENTS[table_name])}"
            )
        return
    connection = op.get_bind()
    for table_name, table_comment in ALL_TABLE_COMMENTS.items():
        # Skip tables that haven't been created yet at this point in the
        # migration chain. Such tables are expected to set their own COMMENT
        # either via ``op.create_table(comment=...)`` in their own migration
        # (see ``20260817_17_class_join_requests``) or via a dedicated
        # ``*_table_comments`` migration after the merge heads (see
        # ``20260817_18_feedback_table_comments``). Without this guard the
        # bulk ALTER fails on MySQL with "Table doesn't exist" whenever a new
        # table is added to ``TABLE_COMMENTS`` after this revision.
        if not connection.dialect.has_table(connection, table_name):
            continue
        op.execute(
            f"ALTER TABLE `{table_name}` COMMENT = "
            f"{_mysql_string_literal(table_comment)}"
        )


def downgrade() -> None:
    if context.is_offline_mode():
        for table_name in _TABLES_AVAILABLE_AT_REVISION:
            op.execute(
                f"ALTER TABLE `{table_name}` COMMENT = "
                f"{_mysql_string_literal('')}"
            )
        return
    connection = op.get_bind()
    for table_name in ALL_TABLE_COMMENTS:
        if not connection.dialect.has_table(connection, table_name):
            continue
        op.execute(
            f"ALTER TABLE `{table_name}` COMMENT = "
            f"{_mysql_string_literal('')}"
        )
