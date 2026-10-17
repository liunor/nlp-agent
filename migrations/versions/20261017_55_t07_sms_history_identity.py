"""Keep active verification codes unique while retaining SMS send history."""

from alembic import context, op
import sqlalchemy as sa

revision = "20261017_55_t07_sms_history"
down_revision = "20261017_54_t07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("nlp_auth_codes", sa.Column(
        "active_subject", sa.String(254),
        sa.Computed("CASE WHEN kind IN ('sms_history','sms_attempt') THEN NULL ELSE subject END", persisted=True),
        nullable=True,
    ))
    # Replace atomically so CAPTCHA/email/usable SMS uniqueness is never lost.
    op.execute(
        "ALTER TABLE nlp_auth_codes "
        "DROP INDEX uq_nlp_auth_codes_kind_subject, "
        "ADD CONSTRAINT uq_nlp_auth_codes_kind_subject UNIQUE (kind,active_subject)"
    )


def downgrade() -> None:
    if not context.is_offline_mode():
        duplicate = op.get_bind().execute(sa.text(
            "SELECT 1 FROM nlp_auth_codes GROUP BY kind,subject HAVING COUNT(*)>1 LIMIT 1"
        )).first()
        if duplicate is not None:
            raise RuntimeError("Archive duplicate SMS history before downgrade; no history has been deleted")
    op.execute(
        "ALTER TABLE nlp_auth_codes "
        "DROP INDEX uq_nlp_auth_codes_kind_subject, "
        "ADD CONSTRAINT uq_nlp_auth_codes_kind_subject UNIQUE (kind,subject)"
    )
    op.drop_column("nlp_auth_codes", "active_subject")
