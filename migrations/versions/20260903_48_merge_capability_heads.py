"""Merge capability metering and user-management migration branches."""

revision = "20260903_48_merge_cap_usage"
down_revision = (
    "20260903_47_sms_send_locks",
    "20260902_45_cap_usage_metering",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
