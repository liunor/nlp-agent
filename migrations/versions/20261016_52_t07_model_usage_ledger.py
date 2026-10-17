"""Add durable model quota reservations and idempotent usage events."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "20261016_52_t07"
down_revision = "20261016_51_t07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nlp_model_quota_accounts",
        sa.Column("user_id", sa.String(36, collation="ascii_bin"), primary_key=True),
        sa.Column("quota_tokens", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("reserved_tokens", mysql.BIGINT(unsigned=True), nullable=False, server_default=sa.text("0")),
        sa.Column("consumed_tokens", mysql.BIGINT(unsigned=True), nullable=False, server_default=sa.text("0")),
        sa.Column("unknown_usage_events", mysql.BIGINT(unsigned=True), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("(UTC_TIMESTAMP(6))")),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("(UTC_TIMESTAMP(6))")),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        comment="用户模型 Token 额度账户及累计预留、消耗和未知用量。",
    )
    op.create_table(
        "nlp_model_quota_reservations",
        sa.Column("id", sa.String(36, collation="ascii_bin"), primary_key=True),
        sa.Column("user_id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("workspace_id", sa.String(36, collation="ascii_bin"), nullable=True),
        sa.Column("turn_id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("reserved_tokens", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("used_tokens", mysql.BIGINT(unsigned=True), nullable=False, server_default=sa.text("0")),
        sa.Column("unknown_usage_events", mysql.BIGINT(unsigned=True), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(16), nullable=False, server_default="reserved"),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("(UTC_TIMESTAMP(6))")),
        sa.Column("finalized_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["nlp_model_quota_accounts.user_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("turn_id", name="uq_nlp_model_quota_reservations_turn_id"),
        sa.Index("ix_nlp_model_quota_reservations_user_status", "user_id", "status"),
        sa.Index("ix_nlp_model_quota_reservations_status_turn", "status", "turn_id"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        comment="Turn 提交时创建并在终态结算的模型 Token 预留。",
    )
    op.create_table(
        "nlp_model_usage_events",
        sa.Column("operation_id", sa.String(36, collation="ascii_bin"), primary_key=True),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("reservation_id", sa.String(36, collation="ascii_bin"), nullable=True),
        sa.Column("user_id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("workspace_id", sa.String(36, collation="ascii_bin"), nullable=True),
        sa.Column("conversation_id", sa.String(128, collation="ascii_bin"), nullable=True),
        sa.Column("turn_id", sa.String(36, collation="ascii_bin"), nullable=True),
        sa.Column("worker_id", sa.String(128), nullable=True),
        sa.Column("parent_operation_id", sa.String(36, collation="ascii_bin"), nullable=True),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_model", sa.String(128), nullable=False),
        sa.Column("model_profile", sa.String(64), nullable=True),
        sa.Column("preset", sa.String(128), nullable=False),
        sa.Column("route", sa.String(128), nullable=True),
        sa.Column("pricing_key", sa.String(160), nullable=True),
        sa.Column("attempt", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("fallback_index", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("input_tokens", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("cached_input_tokens", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("cache_write_input_tokens", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("output_tokens", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("reasoning_output_tokens", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("total_tokens", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("usage_source", sa.String(16), nullable=False),
        sa.Column("provider_response_id", sa.String(256), nullable=True),
        sa.Column("outcome_status", sa.String(16), nullable=False),
        sa.Column("finish_reason", sa.String(128), nullable=True),
        sa.Column("error_kind", sa.String(128), nullable=True),
        sa.Column("started_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("completed_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("payload_sha256", sa.String(64, collation="ascii_bin"), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("(UTC_TIMESTAMP(6))")),
        sa.ForeignKeyConstraint(["reservation_id"], ["nlp_model_quota_reservations.id"], ondelete="SET NULL"),
        sa.Index("ix_nlp_model_usage_events_user_completed", "user_id", "completed_at"),
        sa.Index("ix_nlp_model_usage_events_turn", "turn_id", "completed_at"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        comment="按 Provider Attempt 幂等保存的模型用量事实。",
    )
    op.add_column(
        "nlp_turns",
        sa.Column("quota_reservation_id", sa.String(36, collation="ascii_bin"), nullable=True),
    )
    op.create_index("ix_nlp_turns_quota_reservation_id", "nlp_turns", ["quota_reservation_id"])
    op.create_foreign_key(
        "fk_nlp_turns_quota_reservation_id_nlp_model_quota_reservations",
        "nlp_turns",
        "nlp_model_quota_reservations",
        ["quota_reservation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        INSERT INTO nlp_model_quota_accounts(user_id)
        SELECT DISTINCT user_id FROM nlp_turns WHERE status IN ('accepted','running')
        ON DUPLICATE KEY UPDATE user_id=VALUES(user_id)
        """
    )
    op.execute(
        """
        INSERT INTO nlp_model_quota_reservations
            (id,user_id,workspace_id,turn_id,reserved_tokens,status)
        SELECT UUID(),user_id,workspace_id,id,0,'reserved'
        FROM nlp_turns WHERE status IN ('accepted','running')
        """
    )
    op.execute(
        """
        UPDATE nlp_turns t JOIN nlp_model_quota_reservations r ON r.turn_id=t.id
        SET t.quota_reservation_id=r.id
        WHERE t.status IN ('accepted','running')
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_nlp_turns_quota_au AFTER UPDATE ON nlp_turns
        FOR EACH ROW
        BEGIN
            DECLARE v_reservation_status VARCHAR(16);
            DECLARE v_reserved BIGINT UNSIGNED;
            DECLARE v_unknown BIGINT UNSIGNED;
            DECLARE v_user_id VARCHAR(36);
            IF OLD.status IN ('accepted','running')
               AND NEW.status NOT IN ('accepted','running')
               AND NEW.quota_reservation_id IS NOT NULL THEN
                SELECT status,reserved_tokens,unknown_usage_events,user_id
                INTO v_reservation_status,v_reserved,v_unknown,v_user_id
                FROM nlp_model_quota_reservations
                WHERE id=NEW.quota_reservation_id FOR UPDATE;
                IF v_reservation_status='reserved' THEN
                    UPDATE nlp_model_quota_accounts
                    SET reserved_tokens=reserved_tokens-v_reserved,updated_at=UTC_TIMESTAMP(6)
                    WHERE user_id=v_user_id AND reserved_tokens >= v_reserved;
                    IF ROW_COUNT() = 0 THEN
                        SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO=1644, MESSAGE_TEXT='model_quota_reservation_underflow';
                    END IF;
                    UPDATE nlp_model_quota_reservations
                    SET status=IF(v_unknown > 0,'unknown_usage','settled'),finalized_at=UTC_TIMESTAMP(6)
                    WHERE id=NEW.quota_reservation_id AND status='reserved';
                END IF;
            END IF;
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_nlp_turns_quota_ad AFTER DELETE ON nlp_turns
        FOR EACH ROW
        BEGIN
            DECLARE v_reservation_status VARCHAR(16);
            DECLARE v_reserved BIGINT UNSIGNED;
            DECLARE v_unknown BIGINT UNSIGNED;
            DECLARE v_user_id VARCHAR(36);
            IF OLD.status IN ('accepted','running') AND OLD.quota_reservation_id IS NOT NULL THEN
                SELECT status,reserved_tokens,unknown_usage_events,user_id
                INTO v_reservation_status,v_reserved,v_unknown,v_user_id
                FROM nlp_model_quota_reservations
                WHERE id=OLD.quota_reservation_id FOR UPDATE;
                IF v_reservation_status='reserved' THEN
                    UPDATE nlp_model_quota_accounts
                    SET reserved_tokens=reserved_tokens-v_reserved,updated_at=UTC_TIMESTAMP(6)
                    WHERE user_id=v_user_id AND reserved_tokens >= v_reserved;
                    IF ROW_COUNT() = 0 THEN
                        SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO=1644, MESSAGE_TEXT='model_quota_reservation_underflow';
                    END IF;
                    UPDATE nlp_model_quota_reservations
                    SET status=IF(v_unknown > 0,'unknown_usage','released'),finalized_at=UTC_TIMESTAMP(6)
                    WHERE id=OLD.quota_reservation_id AND status='reserved';
                END IF;
            END IF;
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_nlp_turns_quota_ad")
    op.execute("DROP TRIGGER IF EXISTS trg_nlp_turns_quota_au")
    op.drop_constraint(
        "fk_nlp_turns_quota_reservation_id_nlp_model_quota_reservations",
        "nlp_turns",
        type_="foreignkey",
    )
    op.drop_index("ix_nlp_turns_quota_reservation_id", table_name="nlp_turns")
    op.drop_column("nlp_turns", "quota_reservation_id")
    op.drop_table("nlp_model_usage_events")
    op.drop_table("nlp_model_quota_reservations")
    op.drop_table("nlp_model_quota_accounts")
