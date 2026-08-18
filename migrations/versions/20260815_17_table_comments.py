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


def _set_table_comments(*, clear: bool) -> None:
    for table_name, table_comment in ALL_TABLE_COMMENTS.items():
        op.execute(
            f"ALTER TABLE `{table_name}` COMMENT = "
            f"{_mysql_string_literal('' if clear else table_comment)}"
        )


def upgrade() -> None:
    _set_table_comments(clear=False)


def downgrade() -> None:
    _set_table_comments(clear=True)
