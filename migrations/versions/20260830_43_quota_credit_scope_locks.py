"""Make credit operation idempotency and reset scopes transactional."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

from server.quota.models import QuotaCreditScopeLockModel


revision = "20260830_43_quota_scope_lock"
down_revision = "20260830_42_quota_phase4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column["name"]
        for column in inspector.get_columns("nlp_quota_credit_operations")
    }
    if "effective_from" not in columns:
        op.add_column(
            "nlp_quota_credit_operations",
            sa.Column("effective_from", DATETIME(fsp=6), nullable=True),
        )
    if "expires_at" not in columns:
        op.add_column(
            "nlp_quota_credit_operations",
            sa.Column("expires_at", DATETIME(fsp=6), nullable=True),
        )
    # Existing rows were created with an effective grant timestamp but the
    # Phase 4 table did not persist it.  Backfill from the grant before making
    # the column strict on MySQL.
    if bind.dialect.name == "mysql":
        op.execute(
            sa.text(
                """
                UPDATE nlp_quota_credit_operations AS operation
                INNER JOIN nlp_quota_grants AS grant_row
                    ON grant_row.id = operation.grant_id
                SET operation.effective_from = grant_row.effective_from,
                    operation.expires_at = grant_row.expires_at
                WHERE operation.effective_from IS NULL
                """
            )
        )
        op.alter_column(
            "nlp_quota_credit_operations",
            "effective_from",
            existing_type=DATETIME(fsp=6),
            nullable=False,
        )
    else:
        op.execute(
            sa.text(
                """
                UPDATE nlp_quota_credit_operations
                SET effective_from = (
                    SELECT effective_from
                    FROM nlp_quota_grants
                    WHERE nlp_quota_grants.id = nlp_quota_credit_operations.grant_id
                )
                WHERE effective_from IS NULL
                """
            )
        )
        # SQLite cannot alter a nullable column to NOT NULL portably.  Fresh
        # SQLite test schemas use the ORM model, while production MySQL gets
        # the strict constraint above.
    QuotaCreditScopeLockModel.__table__.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    QuotaCreditScopeLockModel.__table__.drop(bind=bind, checkfirst=True)
    inspector = sa.inspect(bind)
    columns = {
        column["name"]
        for column in inspector.get_columns("nlp_quota_credit_operations")
    }
    if "expires_at" in columns:
        op.drop_column("nlp_quota_credit_operations", "expires_at")
    if "effective_from" in columns:
        op.drop_column("nlp_quota_credit_operations", "effective_from")
