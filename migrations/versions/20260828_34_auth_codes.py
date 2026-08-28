"""shared DB-backed store for captcha and SMS verification codes.

Moves one-time verification codes out of per-process memory so that
generation and verification can happen on different instances, and records
the sender's IP so the SMS endpoint can enforce server-side rate limits.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME


revision = "20260828_34_auth_codes"
down_revision = "20260828_33_user_phone"
branch_labels = None
depends_on = None

UUID = sa.String(36, collation="ascii_bin")


def upgrade() -> None:
    op.create_table(
        "nlp_auth_codes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("subject", sa.String(64), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", DATETIME(fsp=6), nullable=False),
        sa.Column("client_ip", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            DATETIME(fsp=6),
            server_default=sa.text("utc_timestamp(6)"),
            nullable=False,
        ),
        mysql_charset="utf8mb4",
        mysql_comment="图形/短信一次性验证码的哈希存储，含过期时间与发送频控记录。",
    )
    op.create_index("ix_nlp_auth_codes_kind_subject", "nlp_auth_codes", ["kind", "subject"])
    op.create_index(
        "ix_nlp_auth_codes_kind_ip_created", "nlp_auth_codes", ["kind", "client_ip", "created_at"]
    )
    op.create_index("ix_nlp_auth_codes_expires_at", "nlp_auth_codes", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_nlp_auth_codes_expires_at", table_name="nlp_auth_codes")
    op.drop_index("ix_nlp_auth_codes_kind_ip_created", table_name="nlp_auth_codes")
    op.drop_index("ix_nlp_auth_codes_kind_subject", table_name="nlp_auth_codes")
    op.drop_table("nlp_auth_codes")
