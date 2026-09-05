"""Merge quota and authentication hardening migration branches."""

revision = "20260902_45_merge_auth_quota"
down_revision = ("20260901_44_quota_summary", "20260831_43_auth_code_identity")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
