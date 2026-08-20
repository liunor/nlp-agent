"""Document the WebSocket ticket table in MySQL metadata."""

from alembic import op


revision = "20260820_23"
down_revision = "20260819_22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE `nlp_ws_tickets` COMMENT = "
        "'绑定登录会话的一次性 WebSocket 连接票据。'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE `nlp_ws_tickets` COMMENT = ''")
