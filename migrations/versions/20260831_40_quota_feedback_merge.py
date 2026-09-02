"""Merge the quota and feedback migration branches.

Both branches were developed from the shared usage-index revision.  This
revision intentionally has no schema operations; it only makes the graph
unambiguous for fresh installs and upgrades.
"""

from __future__ import annotations

revision = "20260831_40_quota_feedback_merge"
down_revision = ("20260830_43_quota_scope_lock", "20260831_39_feedback_student")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
