"""Complete quota account, reservation, and usage accounting metadata."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "20261017_53_t07"
down_revision = "20261016_52_t07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "nlp_model_quota_accounts",
        sa.Column("token_limit", mysql.BIGINT(unsigned=True), nullable=True),
    )
    op.add_column(
        "nlp_model_quota_accounts",
        sa.Column("window_started_at", mysql.DATETIME(fsp=6), nullable=True),
    )
    op.add_column(
        "nlp_model_quota_accounts",
        sa.Column("window_ends_at", mysql.DATETIME(fsp=6), nullable=True),
    )
    op.add_column(
        "nlp_model_quota_accounts",
        sa.Column(
            "used_tokens",
            mysql.BIGINT(unsigned=True),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "nlp_model_quota_reservations",
        sa.Column("model_profile", sa.String(64), nullable=True),
    )
    op.add_column(
        "nlp_model_quota_reservations",
        sa.Column(
            "settled_tokens",
            mysql.BIGINT(unsigned=True),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "nlp_model_quota_reservations",
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=True),
    )
    op.add_column(
        "nlp_model_usage_events",
        sa.Column("pricing_version", sa.String(64), nullable=True),
    )
    op.add_column(
        "nlp_model_usage_events",
        sa.Column("charge_units", mysql.BIGINT(unsigned=True), nullable=True),
    )
    op.execute(
        "UPDATE nlp_model_quota_accounts "
        "SET token_limit=quota_tokens,used_tokens=consumed_tokens"
    )
    op.execute(
        "UPDATE nlp_model_quota_reservations "
        "SET settled_tokens=used_tokens WHERE status<>'reserved'"
    )
    op.execute(
        """
        CREATE TRIGGER trg_nlp_turns_quota_settle_au AFTER UPDATE ON nlp_turns
        FOR EACH ROW FOLLOWS trg_nlp_turns_quota_au
        BEGIN
            IF OLD.status IN ('accepted','running')
               AND NEW.status NOT IN ('accepted','running')
               AND NEW.quota_reservation_id IS NOT NULL THEN
                UPDATE nlp_model_quota_reservations
                SET settled_tokens=used_tokens,expires_at=NULL
                WHERE id=NEW.quota_reservation_id;
            END IF;
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_nlp_turns_quota_settle_ad AFTER DELETE ON nlp_turns
        FOR EACH ROW FOLLOWS trg_nlp_turns_quota_ad
        BEGIN
            IF OLD.status IN ('accepted','running')
               AND OLD.quota_reservation_id IS NOT NULL THEN
                UPDATE nlp_model_quota_reservations
                SET settled_tokens=used_tokens,expires_at=NULL
                WHERE id=OLD.quota_reservation_id;
            END IF;
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_nlp_turns_quota_settle_ad")
    op.execute("DROP TRIGGER IF EXISTS trg_nlp_turns_quota_settle_au")
    op.drop_column("nlp_model_usage_events", "charge_units")
    op.drop_column("nlp_model_usage_events", "pricing_version")
    op.drop_column("nlp_model_quota_reservations", "expires_at")
    op.drop_column("nlp_model_quota_reservations", "settled_tokens")
    op.drop_column("nlp_model_quota_reservations", "model_profile")
    op.drop_column("nlp_model_quota_accounts", "used_tokens")
    op.drop_column("nlp_model_quota_accounts", "window_ends_at")
    op.drop_column("nlp_model_quota_accounts", "window_started_at")
    op.drop_column("nlp_model_quota_accounts", "token_limit")
