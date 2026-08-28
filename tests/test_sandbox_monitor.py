from __future__ import annotations

import pytest


def test_sandbox_monitor_router_owns_all_sandbox_observability_routes() -> None:
    from server.sandbox.monitor_controller import create_sandbox_monitor_router

    async def dependency():
        yield None

    async def principal_dependency():
        return None

    router = create_sandbox_monitor_router(
        db_session_dependency=dependency,
        principal_dependency=principal_dependency,
        write_access_dependency=principal_dependency,
    )
    paths = {route.path for route in router.routes}

    assert "/api/v1/observability/sandbox/overview" in paths
    assert "/api/v1/observability/sandbox/logs" in paths
    assert "/api/v1/observability/sandbox/runtimes" in paths
    assert "/api/v1/observability/sandbox/runtimes/{runtime_id}/drain" in paths
    assert "/api/v1/observability/sandbox/executions" in paths
    assert "/api/v1/observability/sandbox/executions/{execution_id}/events" in paths


def test_sandbox_log_payload_is_summary_only() -> None:
    from types import SimpleNamespace

    from server.sandbox.monitoring import execution_log_payload

    payload = execution_log_payload(
        SimpleNamespace(
            id="execution-1",
            status="failed",
            started_at=None,
            completed_at=None,
            created_at=None,
            runtime_instance_id="runtime-1",
            exit_reason="TimeoutError: sandbox execution timed out",
        )
    )

    assert payload == {
        "id": "execution:execution-1:failed",
        "timestamp": None,
        "level": "error",
        "event_type": "execution.failed",
        "execution_id": "execution-1",
        "runtime_id": "runtime-1",
        "message": "TimeoutError: sandbox execution timed out",
    }
    assert "stdout" not in payload
    assert "source" not in payload


def test_sandbox_overview_groups_runtime_states_with_counts() -> None:
    from server.sandbox.monitoring import _runtime_state_counts_query

    query = _runtime_state_counts_query()

    assert len(query.selected_columns) == 2
    assert "count" in str(query).lower()


@pytest.mark.asyncio
async def test_sandbox_logs_apply_retention_to_fallback_timestamps() -> None:
    from server.sandbox.monitoring import sandbox_logs

    class EmptyResult:
        def scalars(self):
            return self

        def all(self):
            return []

    class RecordingDb:
        def __init__(self):
            self.queries = []

        async def execute(self, query):
            self.queries.append(query)
            return EmptyResult()

    db = RecordingDb()
    await sandbox_logs(db, limit=10, since_seconds=60)

    assert len(db.queries) == 2
    assert all("coalesce" in str(query).lower() for query in db.queries)
