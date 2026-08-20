"""document the purpose of every MySQL table

Revision ID: 20260815_17
Revises: 20260813_16
Create Date: 2026-08-15 00:00:00
"""

from alembic import op

from server.infrastructure.mysql.table_comments import (
    SYSTEM_TABLE_COMMENTS,
    TABLE_COMMENTS,
)


revision = "20260815_17"
down_revision = "20260813_16"
branch_labels = None
depends_on = None


_TABLES_CREATED_AFTER = {"nlp_feedback_threads", "nlp_feedback_messages"}

ALL_TABLE_COMMENTS = {
    table_name: table_comment
    for table_name, table_comment in {**TABLE_COMMENTS, **SYSTEM_TABLE_COMMENTS}.items()
    if table_name not in _TABLES_CREATED_AFTER
}


def _mysql_string_literal(value: str) -> str:
    """Return a safely quoted literal for the static migration comments."""

    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
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
    connection = op.get_bind()
    for table_name in ALL_TABLE_COMMENTS:
        if not connection.dialect.has_table(connection, table_name):
            continue
        op.execute(
            f"ALTER TABLE `{table_name}` COMMENT = "
            f"{_mysql_string_literal('')}"
        )
