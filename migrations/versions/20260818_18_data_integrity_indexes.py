"""data-integrity indexes & case-insensitive username uniqueness (阶段4 / P1-3 §4.3)

Adds the missing indexes and the username-normalization unique constraint called
out by review §4.3 / P1:

- ``nlp_sessions``: ``(user_id, revoked_at, expires_at)`` — speeds up the
  "list / revoke / sweep sessions" queries used by the session-revocation work
  (P1-3) and periodic cleanup.
- ``nlp_classroom_members``: ``(classroom_id, status, member_role)`` — speeds up
  "list active members / count by role" queries.
- ``nlp_users.username_lower``: a STORED generated column ``LOWER(username)``
  with a unique index, so ``Alice`` and ``alice`` are rejected as duplicates at
  the database layer (case-insensitive username uniqueness). Being generated, it
  is maintained by MySQL automatically — no application-side writes and no
  backfill needed for existing rows.

Revision ID: 20260818_18
Revises: 20260817_17
"""

from alembic import op
import sqlalchemy as sa


revision = "20260818_18"
down_revision = "20260817_17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_nlp_sessions_user_revoked_expires",
        "nlp_sessions",
        ["user_id", "revoked_at", "expires_at"],
    )
    op.create_index(
        "ix_nlp_classroom_members_class_status_role",
        "nlp_classroom_members",
        ["classroom_id", "status", "member_role"],
    )
    # 生成列由 MySQL 自动计算，NOT NULL 由 GENERATED 保证；唯一索引实施大小写无关去重
    op.execute(
        sa.text(
            "ALTER TABLE nlp_users "
            "ADD COLUMN username_lower VARCHAR(64) "
            "GENERATED ALWAYS AS (LOWER(username)) STORED NOT NULL"
        )
    )
    op.create_index(
        "uq_nlp_users_username_lower",
        "nlp_users",
        ["username_lower"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_nlp_users_username_lower", table_name="nlp_users")
    op.drop_column("nlp_users", "username_lower")
    op.drop_index(
        "ix_nlp_classroom_members_class_status_role", table_name="nlp_classroom_members"
    )
    op.drop_index(
        "ix_nlp_sessions_user_revoked_expires", table_name="nlp_sessions"
    )
