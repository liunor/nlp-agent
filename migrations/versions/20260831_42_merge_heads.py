"""Merge the phone/SMS hardening and session summary migration branches."""

revision = "20260831_42_merge_heads"
down_revision = ("20260831_41_phone_sms_hardening", "20260831_40_summary_merge")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
