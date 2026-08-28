"""sandbox warm-pool runtime states (Phase 2).

Revision ID: 20260825_26_sandbox_warm_pool
Revises: 20260825_25
"""

from alembic import op
import sqlalchemy as sa


revision = "20260825_26_sandbox_warm_pool"
down_revision = "20260825_25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing Phase 0 declaration rows are not runnable.  Future Manager rows
    # explicitly start in ``creating``; this default makes direct inserts safe.
    op.alter_column(
        "nlp_sandbox_runtime_instances",
        "state",
        existing_type=sa.String(16),
        server_default="creating",
    )


def downgrade() -> None:
    op.alter_column(
        "nlp_sandbox_runtime_instances",
        "state",
        existing_type=sa.String(16),
        server_default="declared",
    )
