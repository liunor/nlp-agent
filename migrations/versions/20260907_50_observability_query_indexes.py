"""Add composite indexes used by monitor paging and retention queries."""

from alembic import op


revision = "20260907_50_obs_indexes"
down_revision = "20260907_49_reset_permission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_nlp_observability_kind_created",
        "nlp_observability_records",
        ["kind", "created_at"],
    )
    op.create_index(
        "ix_nlp_observability_kind_trace",
        "nlp_observability_records",
        ["kind", "trace_id"],
    )
    op.create_index(
        "ix_nlp_observability_kind_session_status",
        "nlp_observability_records",
        ["kind", "session_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_nlp_observability_kind_session_status",
        table_name="nlp_observability_records",
    )
    op.drop_index(
        "ix_nlp_observability_kind_trace",
        table_name="nlp_observability_records",
    )
    op.drop_index(
        "ix_nlp_observability_kind_created",
        table_name="nlp_observability_records",
    )
