"""Maintain authoritative MySQL counts for active classroom Turns."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "20261016_51_t07"
down_revision = "20261016_51_t06_phone_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nlp_runtime_capacity",
        sa.Column("scope_key", sa.String(32, collation="ascii_bin"), primary_key=True),
        sa.Column("queued_count", mysql.BIGINT(unsigned=True), nullable=False, server_default=sa.text("0")),
        sa.Column("running_count", mysql.BIGINT(unsigned=True), nullable=False, server_default=sa.text("0")),
        sa.Column("queue_capacity", mysql.INTEGER(unsigned=True), nullable=False, server_default=sa.text("200")),
        sa.Column("per_user_active_limit", mysql.INTEGER(unsigned=True), nullable=False, server_default=sa.text("3")),
        sa.Column("last_reconciled_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("(UTC_TIMESTAMP(6))")),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        comment="持久化课堂 Turn 队列与运行容量计数及全局限额。",
    )
    op.create_table(
        "nlp_turn_user_capacity",
        sa.Column("user_id", sa.String(36, collation="ascii_bin"), primary_key=True),
        sa.Column("active_count", mysql.BIGINT(unsigned=True), nullable=False, server_default=sa.text("0")),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("(UTC_TIMESTAMP(6))")),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        comment="持久化用户待处理与运行中 Turn 的并发计数。",
    )
    op.execute(
        sa.text(
            "INSERT INTO nlp_runtime_capacity "
            "(scope_key,queued_count,running_count,last_reconciled_at) "
            "SELECT 'global:turns', "
            "COALESCE(SUM(status='accepted'),0), "
            "COALESCE(SUM(status='running'),0), UTC_TIMESTAMP(6) "
            "FROM nlp_turns"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO nlp_turn_user_capacity(user_id,active_count) "
            "SELECT user_id,COUNT(*) FROM nlp_turns "
            "WHERE status IN ('accepted','running') GROUP BY user_id"
        )
    )

    op.execute(
        """
        CREATE TRIGGER trg_nlp_turns_capacity_ai AFTER INSERT ON nlp_turns
        FOR EACH ROW
        BEGIN
            DECLARE v_active BIGINT UNSIGNED;
            DECLARE v_limit INT UNSIGNED;
            IF NEW.status = 'accepted' THEN
                UPDATE nlp_runtime_capacity
                SET queued_count=queued_count+1, updated_at=UTC_TIMESTAMP(6)
                WHERE scope_key='global:turns' AND queued_count < queue_capacity;
                IF ROW_COUNT() = 0 THEN
                    SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO=1644, MESSAGE_TEXT='queue_capacity_exceeded';
                END IF;
            ELSEIF NEW.status = 'running' THEN
                UPDATE nlp_runtime_capacity
                SET running_count=running_count+1, updated_at=UTC_TIMESTAMP(6)
                WHERE scope_key='global:turns';
            END IF;

            IF NEW.status IN ('accepted','running') THEN
                INSERT INTO nlp_turn_user_capacity(user_id,active_count,updated_at)
                VALUES(NEW.user_id,1,UTC_TIMESTAMP(6))
                ON DUPLICATE KEY UPDATE active_count=active_count+1, updated_at=UTC_TIMESTAMP(6);
                SELECT active_count INTO v_active
                FROM nlp_turn_user_capacity WHERE user_id=NEW.user_id;
                SELECT per_user_active_limit INTO v_limit
                FROM nlp_runtime_capacity WHERE scope_key='global:turns';
                IF v_active > v_limit THEN
                    SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO=1644, MESSAGE_TEXT='user_turn_limit_reached';
                END IF;
            END IF;
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_nlp_turns_capacity_au AFTER UPDATE ON nlp_turns
        FOR EACH ROW
        BEGIN
            DECLARE v_active BIGINT UNSIGNED;
            DECLARE v_limit INT UNSIGNED;
            IF OLD.user_id <> NEW.user_id
               AND (OLD.status IN ('accepted','running') OR NEW.status IN ('accepted','running')) THEN
                SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO=1644, MESSAGE_TEXT='active_turn_owner_immutable';
            END IF;

            IF OLD.status='accepted' AND NEW.status='running' THEN
                UPDATE nlp_runtime_capacity
                SET queued_count=queued_count-1, running_count=running_count+1, updated_at=UTC_TIMESTAMP(6)
                WHERE scope_key='global:turns' AND queued_count > 0;
                IF ROW_COUNT() = 0 THEN
                    SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO=1644, MESSAGE_TEXT='turn_capacity_counter_underflow';
                END IF;
            ELSEIF OLD.status='running' AND NEW.status='accepted' THEN
                UPDATE nlp_runtime_capacity
                SET running_count=running_count-1, queued_count=queued_count+1, updated_at=UTC_TIMESTAMP(6)
                WHERE scope_key='global:turns' AND running_count > 0;
                IF ROW_COUNT() = 0 THEN
                    SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO=1644, MESSAGE_TEXT='turn_capacity_counter_underflow';
                END IF;
            ELSEIF OLD.status='accepted' AND NEW.status NOT IN ('accepted','running') THEN
                UPDATE nlp_runtime_capacity
                SET queued_count=queued_count-1, updated_at=UTC_TIMESTAMP(6)
                WHERE scope_key='global:turns' AND queued_count > 0;
                IF ROW_COUNT() = 0 THEN
                    SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO=1644, MESSAGE_TEXT='turn_capacity_counter_underflow';
                END IF;
                UPDATE nlp_turn_user_capacity
                SET active_count=active_count-1, updated_at=UTC_TIMESTAMP(6)
                WHERE user_id=OLD.user_id AND active_count > 0;
                IF ROW_COUNT() = 0 THEN
                    SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO=1644, MESSAGE_TEXT='turn_capacity_counter_underflow';
                END IF;
            ELSEIF OLD.status='running' AND NEW.status NOT IN ('accepted','running') THEN
                UPDATE nlp_runtime_capacity
                SET running_count=running_count-1, updated_at=UTC_TIMESTAMP(6)
                WHERE scope_key='global:turns' AND running_count > 0;
                IF ROW_COUNT() = 0 THEN
                    SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO=1644, MESSAGE_TEXT='turn_capacity_counter_underflow';
                END IF;
                UPDATE nlp_turn_user_capacity
                SET active_count=active_count-1, updated_at=UTC_TIMESTAMP(6)
                WHERE user_id=OLD.user_id AND active_count > 0;
                IF ROW_COUNT() = 0 THEN
                    SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO=1644, MESSAGE_TEXT='turn_capacity_counter_underflow';
                END IF;
            ELSEIF OLD.status NOT IN ('accepted','running') AND NEW.status='accepted' THEN
                UPDATE nlp_runtime_capacity
                SET queued_count=queued_count+1, updated_at=UTC_TIMESTAMP(6)
                WHERE scope_key='global:turns' AND queued_count < queue_capacity;
                IF ROW_COUNT() = 0 THEN
                    SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO=1644, MESSAGE_TEXT='queue_capacity_exceeded';
                END IF;
                INSERT INTO nlp_turn_user_capacity(user_id,active_count,updated_at)
                VALUES(NEW.user_id,1,UTC_TIMESTAMP(6))
                ON DUPLICATE KEY UPDATE active_count=active_count+1, updated_at=UTC_TIMESTAMP(6);
                SELECT active_count INTO v_active
                FROM nlp_turn_user_capacity WHERE user_id=NEW.user_id;
                SELECT per_user_active_limit INTO v_limit
                FROM nlp_runtime_capacity WHERE scope_key='global:turns';
                IF v_active > v_limit THEN
                    SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO=1644, MESSAGE_TEXT='user_turn_limit_reached';
                END IF;
            ELSEIF OLD.status NOT IN ('accepted','running') AND NEW.status='running' THEN
                UPDATE nlp_runtime_capacity
                SET running_count=running_count+1, updated_at=UTC_TIMESTAMP(6)
                WHERE scope_key='global:turns';
                INSERT INTO nlp_turn_user_capacity(user_id,active_count,updated_at)
                VALUES(NEW.user_id,1,UTC_TIMESTAMP(6))
                ON DUPLICATE KEY UPDATE active_count=active_count+1, updated_at=UTC_TIMESTAMP(6);
                SELECT active_count INTO v_active
                FROM nlp_turn_user_capacity WHERE user_id=NEW.user_id;
                SELECT per_user_active_limit INTO v_limit
                FROM nlp_runtime_capacity WHERE scope_key='global:turns';
                IF v_active > v_limit THEN
                    SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO=1644, MESSAGE_TEXT='user_turn_limit_reached';
                END IF;
            END IF;
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_nlp_turns_capacity_ad AFTER DELETE ON nlp_turns
        FOR EACH ROW
        BEGIN
            IF OLD.status='accepted' THEN
                UPDATE nlp_runtime_capacity
                SET queued_count=queued_count-1, updated_at=UTC_TIMESTAMP(6)
                WHERE scope_key='global:turns' AND queued_count > 0;
                IF ROW_COUNT() = 0 THEN
                    SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO=1644, MESSAGE_TEXT='turn_capacity_counter_underflow';
                END IF;
            ELSEIF OLD.status='running' THEN
                UPDATE nlp_runtime_capacity
                SET running_count=running_count-1, updated_at=UTC_TIMESTAMP(6)
                WHERE scope_key='global:turns' AND running_count > 0;
                IF ROW_COUNT() = 0 THEN
                    SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO=1644, MESSAGE_TEXT='turn_capacity_counter_underflow';
                END IF;
            END IF;

            IF OLD.status IN ('accepted','running') THEN
                UPDATE nlp_turn_user_capacity
                SET active_count=active_count-1, updated_at=UTC_TIMESTAMP(6)
                WHERE user_id=OLD.user_id AND active_count > 0;
                IF ROW_COUNT() = 0 THEN
                    SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO=1644, MESSAGE_TEXT='turn_capacity_counter_underflow';
                END IF;
            END IF;
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_nlp_turns_capacity_ad")
    op.execute("DROP TRIGGER IF EXISTS trg_nlp_turns_capacity_au")
    op.execute("DROP TRIGGER IF EXISTS trg_nlp_turns_capacity_ai")
    op.drop_table("nlp_turn_user_capacity")
    op.drop_table("nlp_runtime_capacity")
