"""Canonical phone identities and durable SMS send-rate audit records."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME
import re

revision = "20260831_41_phone_sms_hardening"
down_revision = "20260830_40_role_descriptions"
branch_labels = None
depends_on = None


def _legacy_normalize(value: str | None) -> str | None:
    raw = str(value or "").strip()
    compact = re.sub(r"[\s().\-\.]", "", raw)
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    if compact.startswith("+"):
        digits = compact[1:]
    else:
        digits = compact
        if len(digits) == 11 and digits.startswith("1"):
            digits = "86" + digits
    if digits.isdigit() and 7 <= len(digits) <= 15:
        return "+" + digits
    return None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("nlp_users")}
    if "phone_number_normalized" not in columns:
        op.add_column("nlp_users", sa.Column("phone_number_normalized", sa.String(16), nullable=True))

    # This migration also repairs deployments that added the column but
    # failed before backfill/index creation. Do not make the repair path
    # depend on whether the column was added in this invocation.
    rows = bind.execute(sa.text(
        "SELECT id, phone_number FROM nlp_users "
        "WHERE phone_number IS NOT NULL AND phone_number_normalized IS NULL"
    )).fetchall()
    for user_id, phone in rows:
        normalized = _legacy_normalize(phone)
        if normalized:
            bind.execute(
                sa.text(
                    "UPDATE nlp_users SET phone_number_normalized=:phone "
                    "WHERE id=:id"
                ),
                {"phone": normalized, "id": user_id},
            )

    # Preserve every account, but keep the oldest canonical identity when
    # historical rows collide. The remaining rows can still log in by username.
    duplicates = bind.execute(sa.text(
        "SELECT phone_number_normalized FROM nlp_users "
        "WHERE phone_number_normalized IS NOT NULL "
        "GROUP BY phone_number_normalized HAVING COUNT(*) > 1"
    )).fetchall()
    for (phone,) in duplicates:
        ids = bind.execute(
            sa.text(
                "SELECT id FROM nlp_users WHERE phone_number_normalized=:phone "
                "ORDER BY created_at, id"
            ),
            {"phone": phone},
        ).fetchall()
        for (user_id,) in ids[1:]:
            bind.execute(
                sa.text(
                    "UPDATE nlp_users SET phone_number_normalized=NULL WHERE id=:id"
                ),
                {"id": user_id},
            )

    inspector = sa.inspect(bind)
    unique_constraints = inspector.get_unique_constraints("nlp_users")
    unique_indexes = inspector.get_indexes("nlp_users")
    has_phone_unique = any(
        item.get("name") == "uq_nlp_users_phone_number_normalized"
        or (
            item.get("unique")
            and item.get("column_names") == ["phone_number_normalized"]
        )
        for item in [*unique_constraints, *unique_indexes]
    )
    if not has_phone_unique:
        op.create_unique_constraint("uq_nlp_users_phone_number_normalized", "nlp_users", ["phone_number_normalized"])
    index_names = {item.get("name") for item in unique_indexes}
    if "ix_nlp_users_phone_number_normalized" not in index_names:
        op.create_index("ix_nlp_users_phone_number_normalized", "nlp_users", ["phone_number_normalized"])

    inspector = sa.inspect(bind)
    if "nlp_sms_send_audits" not in inspector.get_table_names():
        op.create_table(
            "nlp_sms_send_audits",
            sa.Column("id", sa.String(36, collation="ascii_bin"), primary_key=True),
            sa.Column("phone_number", sa.String(16), nullable=False),
            sa.Column("client_ip", sa.String(64), nullable=True),
            sa.Column("outcome", sa.String(16), nullable=False, server_default="sent"),
            sa.Column("created_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("utc_timestamp(6)")),
            comment="短信发送审计记录，独立于可消费的一次性验证码保存，用于可靠频控。",
            mysql_charset="utf8mb4",
        )
        op.create_index("ix_nlp_sms_send_audits_phone_created", "nlp_sms_send_audits", ["phone_number", "created_at"])
        op.create_index("ix_nlp_sms_send_audits_ip_created", "nlp_sms_send_audits", ["client_ip", "created_at"])
    else:
        # A partially applied deployment may have the table without one of
        # its indexes. Repair the missing pieces instead of treating the table
        # as complete.
        index_names = {
            item.get("name")
            for item in sa.inspect(bind).get_indexes("nlp_sms_send_audits")
        }
        if "ix_nlp_sms_send_audits_phone_created" not in index_names:
            op.create_index(
                "ix_nlp_sms_send_audits_phone_created",
                "nlp_sms_send_audits",
                ["phone_number", "created_at"],
            )
        if "ix_nlp_sms_send_audits_ip_created" not in index_names:
            op.create_index(
                "ix_nlp_sms_send_audits_ip_created",
                "nlp_sms_send_audits",
                ["client_ip", "created_at"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "nlp_sms_send_audits" in inspector.get_table_names():
        index_names = {
            item.get("name")
            for item in inspector.get_indexes("nlp_sms_send_audits")
        }
        if "ix_nlp_sms_send_audits_ip_created" in index_names:
            op.drop_index("ix_nlp_sms_send_audits_ip_created", table_name="nlp_sms_send_audits")
        if "ix_nlp_sms_send_audits_phone_created" in index_names:
            op.drop_index("ix_nlp_sms_send_audits_phone_created", table_name="nlp_sms_send_audits")
        op.drop_table("nlp_sms_send_audits")
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("nlp_users")}
    if "phone_number_normalized" in columns:
        unique_names = {
            item.get("name")
            for item in inspector.get_unique_constraints("nlp_users")
        }
        if "uq_nlp_users_phone_number_normalized" in unique_names:
            op.drop_constraint("uq_nlp_users_phone_number_normalized", "nlp_users", type_="unique")
        index_names = {item.get("name") for item in inspector.get_indexes("nlp_users")}
        if "ix_nlp_users_phone_number_normalized" in index_names:
            op.drop_index("ix_nlp_users_phone_number_normalized", table_name="nlp_users")
        op.drop_column("nlp_users", "phone_number_normalized")
