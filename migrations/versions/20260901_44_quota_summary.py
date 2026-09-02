"""Merge the quota-management and session-summary migration heads."""

from __future__ import annotations


revision = "20260901_44_quota_summary"
down_revision = ("20260901_43_role_credit_ops", "20260831_40_summary_merge")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
