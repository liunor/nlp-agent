"""Track cache measurement coverage and repair legacy model attribution."""

import sqlalchemy as sa
from alembic import context, op


revision = "20260912_54_usage_cache_fix"
down_revision = "20260910_53_whiteboard_library"
branch_labels = None
depends_on = None


def _usage_columns() -> set[str]:
    if context.is_offline_mode():
        return set()
    inspector = sa.inspect(op.get_bind())
    return {
        column["name"]
        for column in inspector.get_columns("nlp_usage_events")
    }


def upgrade() -> None:
    if "cache_status" not in _usage_columns():
        op.add_column(
            "nlp_usage_events",
            sa.Column(
                "cache_status",
                sa.String(16),
                nullable=False,
                server_default=sa.text("'unavailable'"),
            ),
        )

    # Historical rows predate the explicit measurement marker. Positive
    # Provider cache facts are provably measured; all-zero legacy rows remain
    # unavailable because zero-hit and omitted cache metadata are ambiguous.
    op.execute(
        sa.text(
            "UPDATE nlp_usage_events "
            "SET cache_status = 'measured' "
            "WHERE usage_source = 'provider' "
            "AND (cached_input_tokens > 0 "
            "OR cache_miss_input_tokens > 0 "
            "OR cache_write_input_tokens > 0)"
        )
    )

    # The queue consumer runs the top-level Turn but is not an Agent Worker.
    # Coordinator identity is deterministic from its preset/route.
    op.execute(
        sa.text(
            "UPDATE nlp_usage_events "
            "SET purpose = 'coordinator', worker_id = NULL "
            "WHERE preset LIKE 'coordinator-%' OR route = 'coordinator'"
        )
    )

    # For real Worker presets, recover the semantic Worker ID from the model
    # Span written for the same operation. If the Span is unavailable, NULL is
    # more accurate than retaining the queue-consumer process ID.
    op.execute(
        sa.text(
            "UPDATE nlp_usage_events AS usage_event "
            "LEFT JOIN ("
            " SELECT operation_id, MAX(worker_id) AS worker_id "
            " FROM ("
            "  SELECT "
            "   JSON_UNQUOTE(JSON_EXTRACT(payload_json, "
            "    '$.payload.attributes.operation_id')) AS operation_id, "
            "   NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload_json, "
            "    '$.payload.worker_id')), 'null') AS worker_id "
            "  FROM nlp_observability_records "
            "  WHERE kind = 'span' "
            "  AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, "
            "   '$.payload.kind')) = 'model'"
            " ) AS model_spans "
            " WHERE operation_id IS NOT NULL AND operation_id <> '' "
            " GROUP BY operation_id"
            ") AS telemetry "
            "ON telemetry.operation_id = usage_event.operation_id "
            "SET usage_event.purpose = 'worker', "
            "usage_event.worker_id = telemetry.worker_id "
            "WHERE usage_event.preset LIKE 'worker-%' "
            "OR usage_event.route = 'worker'"
        )
    )

    # Utility and vision routes are neither Coordinator nor Agent Worker
    # usage. Only rewrite legacy values that came from the old binary
    # coordinator/worker fallback; explicit compact/memory/etc. values remain.
    op.execute(
        sa.text(
            "UPDATE nlp_usage_events "
            "SET purpose = 'other', worker_id = NULL "
            "WHERE route = 'utility' "
            "AND purpose IN ('coordinator', 'worker')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE nlp_usage_events "
            "SET purpose = 'vision', worker_id = NULL "
            "WHERE route = 'vision-worker' "
            "AND purpose IN ('coordinator', 'worker')"
        )
    )

    op.execute(
        sa.text(
            "UPDATE nlp_usage_events SET worker_id = NULL "
            "WHERE purpose <> 'worker'"
        )
    )


def downgrade() -> None:
    # Attribution repairs are intentionally retained: the former process IDs
    # were not semantic Worker identities and cannot be restored safely.
    if context.is_offline_mode() or "cache_status" in _usage_columns():
        op.drop_column("nlp_usage_events", "cache_status")
