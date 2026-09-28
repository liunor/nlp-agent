"""Merge the developer control-plane cleanup with the latest develop branch."""

revision = "20260904_48_developer_merge"
down_revision = (
    "20260903_46_remove_dev_sessions",
    "20260903_47_sms_send_locks",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
