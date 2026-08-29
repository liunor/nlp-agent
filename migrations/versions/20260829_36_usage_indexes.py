"""add indexes used by audit and Agent Session usage views.

The administration pages are deliberately paginated.  These indexes keep
their count/order queries bounded as the audit and conversation tables grow.
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260829_36_usage_indexes"
down_revision = "20260829_35_user_mgmt_menus"
branch_labels = None
depends_on = None


def _index_names(table_name: str) -> set[str]:
    if context.is_offline_mode():
        return set()
    bind = op.get_bind()
    return {item["name"] for item in sa.inspect(bind).get_indexes(table_name)}


def _create_index_if_missing(
    name: str, table_name: str, columns: list[str]
) -> None:
    if context.is_offline_mode() or name not in _index_names(table_name):
        op.create_index(name, table_name, columns)


def upgrade() -> None:
    _create_index_if_missing(
        "ix_nlp_authorization_audit_created_at",
        "nlp_authorization_audit_logs",
        ["created_at"],
    )
    _create_index_if_missing(
        "ix_nlp_conversations_owner_status_activity",
        "nlp_conversations",
        ["owner_user_id", "status", "last_message_at", "created_at"],
    )


def downgrade() -> None:
    if context.is_offline_mode():
        op.drop_index(
            "ix_nlp_conversations_owner_status_activity",
            table_name="nlp_conversations",
        )
        op.drop_index(
            "ix_nlp_authorization_audit_created_at",
            table_name="nlp_authorization_audit_logs",
        )
        return
    bind = op.get_bind()
    indexes = {
        (table, item["name"])
        for table in (
            "nlp_authorization_audit_logs",
            "nlp_conversations",
        )
        for item in sa.inspect(bind).get_indexes(table)
    }
    if ("nlp_conversations", "ix_nlp_conversations_owner_status_activity") in indexes:
        op.drop_index(
            "ix_nlp_conversations_owner_status_activity",
            table_name="nlp_conversations",
        )
    if ("nlp_authorization_audit_logs", "ix_nlp_authorization_audit_created_at") in indexes:
        op.drop_index(
            "ix_nlp_authorization_audit_created_at",
            table_name="nlp_authorization_audit_logs",
        )
