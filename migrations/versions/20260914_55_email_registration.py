"""Switch self-service registration from SMS to email verification.

Adds email identity columns to ``nlp_users``, creates the email send-audit and
send-lock tables that mirror the former SMS rate-limit machinery, and drops the
now-unused SMS tables and phone columns (the product is not live, so no
back-compat for phone accounts is required).
"""

from alembic import context, op
from sqlalchemy.dialects.mysql import DATETIME
import sqlalchemy as sa


revision = "20260914_55_email_registration"
down_revision = "20260912_54_usage_cache_fix"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _user_columns() -> set:
    return {c["name"] for c in _inspector().get_columns("nlp_users")}


def _upgrade_offline() -> None:
    op.add_column("nlp_users", sa.Column("email", sa.String(254), nullable=True))
    op.create_index("ix_nlp_users_email", "nlp_users", ["email"])
    op.add_column(
        "nlp_users", sa.Column("email_normalized", sa.String(254), nullable=True)
    )
    op.create_unique_constraint(
        "uq_nlp_users_email_normalized", "nlp_users", ["email_normalized"]
    )
    op.create_index(
        "ix_nlp_users_email_normalized", "nlp_users", ["email_normalized"]
    )
    op.create_table(
        "nlp_email_send_audits",
        sa.Column("id", sa.String(36, collation="ascii_bin"), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("client_ip", sa.String(64), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="sent"),
        sa.Column(
            "created_at",
            DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("utc_timestamp(6)"),
        ),
        comment="邮箱验证码发送审计记录，独立于可消费的一次性验证码保存，用于可靠频控。",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_nlp_email_send_audits_email_created",
        "nlp_email_send_audits",
        ["email", "created_at"],
    )
    op.create_index(
        "ix_nlp_email_send_audits_ip_created",
        "nlp_email_send_audits",
        ["client_ip", "created_at"],
    )
    op.create_table(
        "nlp_email_send_locks",
        sa.Column("email", sa.String(254), primary_key=True),
        sa.Column(
            "locked_at",
            DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("utc_timestamp(6)"),
        ),
        comment="邮箱频控的事务锁行，不保存验证码或用户隐私以外的业务数据。",
        mysql_charset="utf8mb4",
    )
    op.drop_index(
        "ix_nlp_sms_send_audits_ip_created", table_name="nlp_sms_send_audits"
    )
    op.drop_index(
        "ix_nlp_sms_send_audits_phone_created", table_name="nlp_sms_send_audits"
    )
    op.drop_table("nlp_sms_send_audits")
    op.drop_table("nlp_sms_send_locks")
    op.drop_constraint(
        "uq_nlp_users_phone_number_normalized", "nlp_users", type_="unique"
    )
    op.drop_index(
        "ix_nlp_users_phone_number_normalized", table_name="nlp_users"
    )
    op.drop_column("nlp_users", "phone_number_normalized")
    op.drop_index("ix_nlp_users_phone_number", table_name="nlp_users")
    op.drop_column("nlp_users", "phone_number")
    op.alter_column(
        "nlp_auth_codes",
        "subject",
        existing_type=sa.String(64),
        type_=sa.String(254),
        existing_nullable=False,
    )
    op.execute(
        "ALTER TABLE `nlp_auth_codes` COMMENT = "
        "'图形/邮箱一次性验证码的哈希存储，含过期时间与发送频控记录。'"
    )


def upgrade() -> None:
    if context.is_offline_mode():
        _upgrade_offline()
        return
    columns = _user_columns()
    if "email" not in columns:
        op.add_column("nlp_users", sa.Column("email", sa.String(254), nullable=True))
        op.create_index("ix_nlp_users_email", "nlp_users", ["email"])
    if "email_normalized" not in columns:
        op.add_column(
            "nlp_users", sa.Column("email_normalized", sa.String(254), nullable=True)
        )
        op.create_unique_constraint(
            "uq_nlp_users_email_normalized", "nlp_users", ["email_normalized"]
        )
        op.create_index(
            "ix_nlp_users_email_normalized", "nlp_users", ["email_normalized"]
        )

    if not _has_table("nlp_email_send_audits"):
        op.create_table(
            "nlp_email_send_audits",
            sa.Column("id", sa.String(36, collation="ascii_bin"), primary_key=True),
            sa.Column("email", sa.String(254), nullable=False),
            sa.Column("client_ip", sa.String(64), nullable=True),
            sa.Column("outcome", sa.String(16), nullable=False, server_default="sent"),
            sa.Column(
                "created_at",
                DATETIME(fsp=6),
                nullable=False,
                server_default=sa.text("utc_timestamp(6)"),
            ),
            comment="邮箱验证码发送审计记录，独立于可消费的一次性验证码保存，用于可靠频控。",
            mysql_charset="utf8mb4",
        )
        op.create_index(
            "ix_nlp_email_send_audits_email_created",
            "nlp_email_send_audits",
            ["email", "created_at"],
        )
        op.create_index(
            "ix_nlp_email_send_audits_ip_created",
            "nlp_email_send_audits",
            ["client_ip", "created_at"],
        )

    if not _has_table("nlp_email_send_locks"):
        op.create_table(
            "nlp_email_send_locks",
            sa.Column("email", sa.String(254), primary_key=True),
            sa.Column(
                "locked_at",
                DATETIME(fsp=6),
                nullable=False,
                server_default=sa.text("utc_timestamp(6)"),
            ),
            comment="邮箱频控的事务锁行，不保存验证码或用户隐私以外的业务数据。",
            mysql_charset="utf8mb4",
        )

    # --- drop the retired SMS machinery -----------------------------------
    if _has_table("nlp_sms_send_audits"):
        index_names = {i["name"] for i in _inspector().get_indexes("nlp_sms_send_audits")}
        if "ix_nlp_sms_send_audits_ip_created" in index_names:
            op.drop_index("ix_nlp_sms_send_audits_ip_created", table_name="nlp_sms_send_audits")
        if "ix_nlp_sms_send_audits_phone_created" in index_names:
            op.drop_index("ix_nlp_sms_send_audits_phone_created", table_name="nlp_sms_send_audits")
        op.drop_table("nlp_sms_send_audits")
    if _has_table("nlp_sms_send_locks"):
        op.drop_table("nlp_sms_send_locks")

    columns = _user_columns()
    if "phone_number_normalized" in columns:
        unique_names = {u["name"] for u in _inspector().get_unique_constraints("nlp_users")}
        if "uq_nlp_users_phone_number_normalized" in unique_names:
            op.drop_constraint(
                "uq_nlp_users_phone_number_normalized", "nlp_users", type_="unique"
            )
        index_names = {i["name"] for i in _inspector().get_indexes("nlp_users")}
        if "ix_nlp_users_phone_number_normalized" in index_names:
            op.drop_index("ix_nlp_users_phone_number_normalized", table_name="nlp_users")
        op.drop_column("nlp_users", "phone_number_normalized")
    columns = _user_columns()
    if "phone_number" in columns:
        index_names = {i["name"] for i in _inspector().get_indexes("nlp_users")}
        if "ix_nlp_users_phone_number" in index_names:
            op.drop_index("ix_nlp_users_phone_number", table_name="nlp_users")
        op.drop_column("nlp_users", "phone_number")

    # The shared auth-code table is created by 20260828_34 with an SMS-worded
    # comment; keep its stored comment aligned with TABLE_COMMENTS.
    if _has_table("nlp_auth_codes"):
        op.alter_column(
            "nlp_auth_codes",
            "subject",
            existing_type=sa.String(64),
            type_=sa.String(254),
            existing_nullable=False,
        )
        op.execute(
            "ALTER TABLE `nlp_auth_codes` COMMENT = "
            "'图形/邮箱一次性验证码的哈希存储，含过期时间与发送频控记录。'"
        )


def downgrade() -> None:
    columns = _user_columns()
    if "phone_number" not in columns:
        op.add_column("nlp_users", sa.Column("phone_number", sa.String(20), nullable=True))
        op.create_index("ix_nlp_users_phone_number", "nlp_users", ["phone_number"])
    if "phone_number_normalized" not in columns:
        op.add_column(
            "nlp_users", sa.Column("phone_number_normalized", sa.String(16), nullable=True)
        )
        op.create_unique_constraint(
            "uq_nlp_users_phone_number_normalized", "nlp_users", ["phone_number_normalized"]
        )
        op.create_index(
            "ix_nlp_users_phone_number_normalized", "nlp_users", ["phone_number_normalized"]
        )

    if not _has_table("nlp_sms_send_locks"):
        op.create_table(
            "nlp_sms_send_locks",
            sa.Column("phone_number", sa.String(16), primary_key=True),
            sa.Column(
                "locked_at",
                DATETIME(fsp=6),
                nullable=False,
                server_default=sa.text("utc_timestamp(6)"),
            ),
            mysql_charset="utf8mb4",
        )
    if not _has_table("nlp_sms_send_audits"):
        op.create_table(
            "nlp_sms_send_audits",
            sa.Column("id", sa.String(36, collation="ascii_bin"), primary_key=True),
            sa.Column("phone_number", sa.String(16), nullable=False),
            sa.Column("client_ip", sa.String(64), nullable=True),
            sa.Column("outcome", sa.String(16), nullable=False, server_default="sent"),
            sa.Column(
                "created_at",
                DATETIME(fsp=6),
                nullable=False,
                server_default=sa.text("utc_timestamp(6)"),
            ),
            mysql_charset="utf8mb4",
        )
        op.create_index(
            "ix_nlp_sms_send_audits_phone_created",
            "nlp_sms_send_audits",
            ["phone_number", "created_at"],
        )
        op.create_index(
            "ix_nlp_sms_send_audits_ip_created",
            "nlp_sms_send_audits",
            ["client_ip", "created_at"],
        )

    if _has_table("nlp_email_send_locks"):
        op.drop_table("nlp_email_send_locks")
    if _has_table("nlp_email_send_audits"):
        index_names = {i["name"] for i in _inspector().get_indexes("nlp_email_send_audits")}
        if "ix_nlp_email_send_audits_ip_created" in index_names:
            op.drop_index("ix_nlp_email_send_audits_ip_created", table_name="nlp_email_send_audits")
        if "ix_nlp_email_send_audits_email_created" in index_names:
            op.drop_index("ix_nlp_email_send_audits_email_created", table_name="nlp_email_send_audits")
        op.drop_table("nlp_email_send_audits")

    columns = _user_columns()
    if "email_normalized" in columns:
        unique_names = {u["name"] for u in _inspector().get_unique_constraints("nlp_users")}
        if "uq_nlp_users_email_normalized" in unique_names:
            op.drop_constraint("uq_nlp_users_email_normalized", "nlp_users", type_="unique")
        index_names = {i["name"] for i in _inspector().get_indexes("nlp_users")}
        if "ix_nlp_users_email_normalized" in index_names:
            op.drop_index("ix_nlp_users_email_normalized", table_name="nlp_users")
        op.drop_column("nlp_users", "email_normalized")
    if "email" in columns:
        index_names = {i["name"] for i in _inspector().get_indexes("nlp_users")}
        if "ix_nlp_users_email" in index_names:
            op.drop_index("ix_nlp_users_email", table_name="nlp_users")
        op.drop_column("nlp_users", "email")

    if _has_table("nlp_auth_codes"):
        op.alter_column(
            "nlp_auth_codes",
            "subject",
            existing_type=sa.String(254),
            type_=sa.String(64),
            existing_nullable=False,
        )
        op.execute(
            "ALTER TABLE `nlp_auth_codes` COMMENT = "
            "'图形/短信一次性验证码的哈希存储，含过期时间与发送频控记录。'"
        )
