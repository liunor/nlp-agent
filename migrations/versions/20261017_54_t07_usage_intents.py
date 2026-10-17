"""Protect model reservations until every dispatched attempt is accounted for."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "20261017_54_t07"
down_revision = "20261017_53_t07"
branch_labels = None
depends_on = None


def _create_release_trigger(event: str, *, protected: bool = True) -> None:
    suffix = "au" if event == "UPDATE" else "ad"
    reference = "NEW" if event == "UPDATE" else "OLD"
    transition = "AND NEW.status NOT IN ('accepted','running')" if event == "UPDATE" else ""
    final_status = "settled" if event == "UPDATE" else "released"
    pending_select = ",pending_invocations" if protected else ""
    pending_into = ",v_pending" if protected else ""
    guard = "AND v_pending=0 AND v_unknown=0" if protected else ""
    settlement = ",settled_tokens=used_tokens,expires_at=NULL" if protected else ""
    status = f"'{final_status}'" if protected else f"IF(v_unknown>0,'unknown_usage','{final_status}')"
    op.execute(f"""
        CREATE TRIGGER trg_nlp_turns_quota_{suffix} AFTER {event} ON nlp_turns
        FOR EACH ROW FOLLOWS trg_nlp_turns_capacity_{suffix}
        BEGIN
            DECLARE v_status VARCHAR(16);
            DECLARE v_reserved BIGINT UNSIGNED;
            DECLARE v_pending INT UNSIGNED;
            DECLARE v_unknown BIGINT UNSIGNED;
            DECLARE v_user VARCHAR(36);
            IF OLD.status IN ('accepted','running') {transition}
               AND {reference}.quota_reservation_id IS NOT NULL THEN
                SELECT status,reserved_tokens,unknown_usage_events,user_id{pending_select}
                INTO v_status,v_reserved,v_unknown,v_user{pending_into}
                FROM nlp_model_quota_reservations
                WHERE id={reference}.quota_reservation_id FOR UPDATE;
                IF v_status='reserved' {guard} THEN
                    UPDATE nlp_model_quota_accounts
                    SET reserved_tokens=reserved_tokens-v_reserved,updated_at=UTC_TIMESTAMP(6)
                    WHERE user_id=v_user AND reserved_tokens>=v_reserved;
                    IF ROW_COUNT()=0 THEN
                        SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO=1644,
                            MESSAGE_TEXT='model_quota_reservation_underflow';
                    END IF;
                    UPDATE nlp_model_quota_reservations
                    SET status={status}{settlement},finalized_at=UTC_TIMESTAMP(6)
                    WHERE id={reference}.quota_reservation_id;
                END IF;
            END IF;
        END
    """)


def upgrade() -> None:
    op.add_column("nlp_model_quota_reservations", sa.Column(
        "pending_invocations", mysql.INTEGER(unsigned=True), nullable=False,
        server_default=sa.text("0"),
    ))
    op.create_table(
        "nlp_model_usage_intents",
        sa.Column("operation_id", sa.String(36, collation="ascii_bin"), primary_key=True),
        sa.Column("reservation_id", sa.String(36, collation="ascii_bin"), nullable=True),
        sa.Column("user_id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("reserved_tokens", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("invocation_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("(UTC_TIMESTAMP(6))")),
        sa.ForeignKeyConstraint(["reservation_id"], ["nlp_model_quota_reservations.id"], ondelete="RESTRICT"),
        sa.Index("ix_nlp_model_usage_intents_status", "status"),
        sa.Index("ix_nlp_model_usage_intents_reservation", "reservation_id", "status"),
        mysql_engine="InnoDB", mysql_charset="utf8mb4",
        comment="模型调用前持久化的预算及待上报状态，未完成计量时保留额度。",
    )
    for suffix in ("au", "ad"):
        op.execute(f"DROP TRIGGER trg_nlp_turns_quota_settle_{suffix}")
        op.execute(f"DROP TRIGGER trg_nlp_turns_quota_{suffix}")
    _create_release_trigger("UPDATE")
    _create_release_trigger("DELETE")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM nlp_model_usage_intents WHERE status IN ('pending','unknown_usage')")).scalar_one():
        raise RuntimeError("resolve all pending/unknown model usage before downgrading")
    for suffix in ("au", "ad"):
        op.execute(f"DROP TRIGGER trg_nlp_turns_quota_{suffix}")
    for event in ("UPDATE", "DELETE"):
        _create_release_trigger(event, protected=False)
        suffix = "au" if event == "UPDATE" else "ad"
        reference = "NEW" if event == "UPDATE" else "OLD"
        transition = "AND NEW.status NOT IN ('accepted','running')" if event == "UPDATE" else ""
        op.execute(f"""
            CREATE TRIGGER trg_nlp_turns_quota_settle_{suffix} AFTER {event} ON nlp_turns
            FOR EACH ROW FOLLOWS trg_nlp_turns_quota_{suffix}
            BEGIN
                IF OLD.status IN ('accepted','running') {transition}
                   AND {reference}.quota_reservation_id IS NOT NULL THEN
                    UPDATE nlp_model_quota_reservations
                    SET settled_tokens=used_tokens,expires_at=NULL
                    WHERE id={reference}.quota_reservation_id;
                END IF;
            END
        """)
    op.drop_table("nlp_model_usage_intents")
    op.drop_column("nlp_model_quota_reservations", "pending_invocations")
