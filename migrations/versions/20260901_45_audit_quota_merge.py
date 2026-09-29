"""Merge the developer audit and quota-management migration heads."""

from __future__ import annotations


revision = "20260901_45_audit_quota_merge"
down_revision = ("20260901_41_monitor_audit", "20260901_44_quota_summary")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
