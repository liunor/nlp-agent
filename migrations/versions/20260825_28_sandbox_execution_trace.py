"""persist trace correlation on sandbox executions (Phase 4)."""

from alembic import op
import sqlalchemy as sa


revision = "20260825_28"
down_revision = "20260825_27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("nlp_sandbox_executions", sa.Column("trace_id", sa.String(64), nullable=True))
    op.add_column("nlp_sandbox_executions", sa.Column("span_id", sa.String(64), nullable=True))
    op.add_column("nlp_sandbox_executions", sa.Column("parent_span_id", sa.String(64), nullable=True))
    op.create_index("ix_nlp_sandbox_executions_trace_id", "nlp_sandbox_executions", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_nlp_sandbox_executions_trace_id", table_name="nlp_sandbox_executions")
    op.drop_column("nlp_sandbox_executions", "parent_span_id")
    op.drop_column("nlp_sandbox_executions", "span_id")
    op.drop_column("nlp_sandbox_executions", "trace_id")
