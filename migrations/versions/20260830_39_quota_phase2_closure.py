from alembic import op

from server.quota.models import QuotaConcurrencyLockModel


revision = "20260830_39_quota_phase2_closure"
down_revision = "20260829_38"
branch_labels = None
depends_on = None


def upgrade() -> None:
    QuotaConcurrencyLockModel.__table__.create(bind=op.get_bind())


def downgrade() -> None:
    QuotaConcurrencyLockModel.__table__.drop(bind=op.get_bind())
