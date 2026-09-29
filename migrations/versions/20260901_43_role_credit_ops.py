"""Persist role-wide credit gift batches.

The individual grants remain user-owned.  This table stores the operator's
batch request and its recipient snapshot so a retry can validate the complete
request and replay the same set of users, even if role membership changes.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import BIGINT, DATETIME


revision = "20260901_43_role_credit_ops"
down_revision = "20260831_42_quota_daily_weekly"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nlp_quota_role_credit_operations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("role_code", sa.String(64), nullable=False),
        sa.Column("bucket_type", sa.String(16), nullable=False),
        sa.Column("period_start", DATETIME(fsp=6), nullable=False),
        sa.Column("period_end", DATETIME(fsp=6), nullable=False),
        sa.Column("amount_micro", BIGINT(unsigned=True), nullable=False),
        sa.Column("actor_user_id", sa.String(128), nullable=False),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("effective_from", DATETIME(fsp=6), nullable=False),
        sa.Column("expires_at", DATETIME(fsp=6), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("recipient_user_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "bucket_type IN ('daily', 'weekly')",
            name="ck_nlp_quota_role_credit_operations_bucket_type",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_nlp_quota_role_credit_operations_idempotency_key",
        ),
        comment="按角色向成员批量赠送 Credits 的幂等批次记录。",
    )
    op.create_index(
        "ix_nlp_quota_role_credit_operations_role_created",
        "nlp_quota_role_credit_operations",
        ["role_code", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_nlp_quota_role_credit_operations_role_created",
        table_name="nlp_quota_role_credit_operations",
    )
    op.drop_table("nlp_quota_role_credit_operations")
