"""Add durable transaction locks for concurrent SMS rate checks."""

from alembic import op
from sqlalchemy.dialects.mysql import DATETIME
import sqlalchemy as sa


revision = "20260903_47_sms_send_locks"
down_revision = "20260903_46_fixed_role_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "nlp_sms_send_locks" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "nlp_sms_send_locks",
            sa.Column("phone_number", sa.String(16), primary_key=True),
            sa.Column(
                "locked_at",
                DATETIME(fsp=6),
                nullable=False,
                server_default=sa.text("utc_timestamp(6)"),
            ),
            comment="短信频控的事务锁行，不保存验证码或用户隐私以外的业务数据。",
            mysql_charset="utf8mb4",
        )


def downgrade() -> None:
    if "nlp_sms_send_locks" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("nlp_sms_send_locks")
