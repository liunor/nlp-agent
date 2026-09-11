from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

import core.observability.runtime as observability_runtime
import core.observability.repository as sqlite_repository_module
import server.monitor.retention as retention_module
from core.observability.context import TelemetryContext
from core.observability.models import SpanKind, SpanRecord, SpanStatus, TelemetryEnvelope, TraceRecord
from core.observability.mysql_repository import MySQLTelemetryRepository
from core.observability.repository import TelemetryRepository
from core.observability.runtime import TelemetryRuntime
from core.observability.summary import build_dependency_health, build_telemetry_overview
from core.identity import AccessDeniedError, AuthenticatedPrincipal
from core.observability.service import ObservabilityService
from core.rbac import Permission
from gateway.mysql_repository import MySQLGatewayRepository
from server.quota.usage import UsageReadService
from server.sandbox.monitoring import list_executions, list_runtimes


def test_span_record_keeps_ttft_as_a_first_class_metric() -> None:
    record = SpanRecord(
        trace_id="trace-1",
        span_id="span-1",
        session_id="session-1",
        turn_id="turn-1",
        kind=SpanKind.MODEL,
        name="model.request",
        status=SpanStatus.OK,
        ttft_ms=42,
    )

    assert record.ttft_ms == 42


def test_dependency_health_reads_legacy_ttft_from_span_attributes() -> None:
    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    result = build_dependency_health(
        traces=[
            {
                "trace_id": "trace-1",
                "user_id": "user-1",
                "workspace_id": "workspace-1",
                "started_at": "2026-09-07T11:59:00+00:00",
                "completed_at": "2026-09-07T11:59:50+00:00",
                "duration_ms": 50000,
                "status": "ok",
            }
        ],
        spans=[
            {
                "trace_id": "trace-1",
                "kind": "model",
                "name": "model.request",
                "started_at": "2026-09-07T11:59:01+00:00",
                "completed_at": "2026-09-07T11:59:40+00:00",
                "duration_ms": 39000,
                "status": "ok",
                "attributes": {
                    "provider": "openai",
                    "model": "gpt-test",
                    "ttft_ms": 123,
                },
            }
        ],
        days=1,
        now=now,
    )

    assert result["models"][0]["ttft_ms"]["p95"] == 123


def test_overview_and_trend_read_legacy_trace_ttft_from_attributes() -> None:
    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    result = build_telemetry_overview(
        traces=[
            {
                "trace_id": "trace-legacy-ttft",
                "user_id": "user-1",
                "workspace_id": "workspace-1",
                "started_at": "2026-09-07T11:59:00+00:00",
                "completed_at": "2026-09-07T11:59:01+00:00",
                "duration_ms": 1000,
                "status": "ok",
                "attributes": {"ttft_ms": 77},
            }
        ],
        spans=[],
        events=[],
        days=1,
        now=now,
    )

    assert result["ttft_ms"]["p95"] == 77

    health = build_dependency_health(
        traces=[
            {
                "trace_id": "trace-legacy-ttft",
                "user_id": "user-1",
                "workspace_id": "workspace-1",
                "started_at": "2026-09-07T11:59:00+00:00",
                "completed_at": "2026-09-07T11:59:01+00:00",
                "duration_ms": 1000,
                "status": "ok",
                "attributes": {"ttft_ms": 77},
            }
        ],
        spans=[],
        days=1,
        now=now,
    )
    assert health["trend"][-1]["ttft_ms"]["p95"] == 77


def test_telemetry_runtime_path_does_not_construct_shared_mysql(monkeypatch) -> None:
    def fail_if_mysql_is_constructed(*_args, **_kwargs):
        raise AssertionError("isolated runtime must not construct MySQL telemetry")

    monkeypatch.setattr(observability_runtime, "MySQLTelemetryRepository", fail_if_mysql_is_constructed)

    path = Path.cwd() / f".audit-isolated-{uuid4().hex}.sqlite3"
    runtime = TelemetryRuntime(path)

    try:
        assert runtime.repository.__class__.__name__ == "TelemetryRepository"
    finally:
        runtime.repository.close()
        for suffix in ("", "-wal", "-shm"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)


def test_isolated_runtime_resetter_does_not_construct_shared_mysql(monkeypatch) -> None:
    from server.monitor import reset as reset_module

    def fail_if_mysql_is_constructed(*_args, **_kwargs):
        raise AssertionError("isolated reset must not construct the shared MySQL gateway")

    monkeypatch.setattr(reset_module, "MySQLGatewayRepository", fail_if_mysql_is_constructed)

    path = Path.cwd() / f".audit-reset-isolated-{uuid4().hex}.sqlite3"
    runtime = TelemetryRuntime(path)

    try:
        resetter = reset_module.LocalRuntimeResetter(runtime)
        assert resetter.gateway_repository is None
    finally:
        runtime.repository.close()
        for suffix in ("", "-wal", "-shm"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)


def test_monitor_permission_can_read_all_user_observability_without_admin_role() -> None:
    calls: dict[str, object] = {}

    class Repository:
        def overview(self, days):
            calls["overview_days"] = days
            return {"scope": "system"}

        def list_traces(self, **kwargs):
            calls["trace_kwargs"] = kwargs
            return []

    class Runtime:
        repository = Repository()

        def health(self):
            return {"status": "ok"}

    principal = AuthenticatedPrincipal(
        user_id="monitor-operator",
        workspace_ids=frozenset({"workspace-1"}),
        permissions=frozenset({Permission.SYSTEM_RUNTIME_MONITOR.value}),
    )
    service = ObservabilityService(Runtime())

    assert asyncio.run(service.overview(principal, days=7))["scope"] == "system"
    assert asyncio.run(service.traces(principal, limit=20)) == []
    assert calls["trace_kwargs"] == {
        "limit": 20,
        "session_id": None,
        "status": None,
        "user_id": None,
        "workspace_ids": None,
    }


def test_observability_service_rejects_a_principal_without_monitor_permission() -> None:
    class Repository:
        def overview(self, **_kwargs):
            raise AssertionError("unauthorized monitor query must not reach the repository")

    class Runtime:
        repository = Repository()

    service = ObservabilityService(Runtime())
    guest = AuthenticatedPrincipal(user_id="guest", roles=frozenset({"guest"}))

    with pytest.raises(AccessDeniedError):
        asyncio.run(service.overview(guest))


@pytest.mark.asyncio
async def test_span_ttft_is_persisted_as_a_top_level_sqlite_metric() -> None:
    path = Path.cwd() / f".audit-span-ttft-{uuid4().hex}.sqlite3"
    runtime = TelemetryRuntime(path, flush_interval_s=0.01)
    context = TelemetryContext.create(
        session_id="session-ttft",
        turn_id="turn-ttft",
        workspace_id="workspace-1",
    )
    runtime.start_trace(context)
    async with runtime.span(SpanKind.MODEL, "model.request", context=context) as span:
        span.annotate(ttft_ms=42)

    try:
        await runtime.flush()
        detail = runtime.repository.trace_detail(context.trace_id)
        assert detail is not None
        assert detail["spans"][0]["ttft_ms"] == 42
    finally:
        await runtime.close()
        for suffix in ("", "-wal", "-shm"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_sqlite_trace_detail_declares_child_limits_and_keeps_chronological_order() -> None:
    path = Path.cwd() / f".audit-trace-detail-{uuid4().hex}.sqlite3"
    repository = TelemetryRepository(path)
    runtime = TelemetryRuntime(path, flush_interval_s=0.01)
    context = TelemetryContext.create(
        session_id="session-detail",
        turn_id="turn-detail",
        workspace_id="workspace-1",
    )
    runtime.start_trace(context)
    async with runtime.span(SpanKind.COORDINATOR, "router.first", context=context):
        pass
    async with runtime.span(SpanKind.TOOL, "tool.second", context=context):
        pass

    try:
        await runtime.flush()
        detail = repository.trace_detail(context.trace_id)
        assert detail is not None
        assert detail["detail_limits"]["children"] == 5_000
        assert [span["name"] for span in detail["spans"]] == [
            "router.first",
            "tool.second",
        ]
    finally:
        await runtime.close()
        repository.close()
        for suffix in ("", "-wal", "-shm"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)


def test_sqlite_trace_group_listing_bounds_raw_rows_and_reports_truncation(monkeypatch) -> None:
    path = Path.cwd() / f".audit-trace-groups-{uuid4().hex}.sqlite3"
    repository = TelemetryRepository(path)
    started_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    repository.write_batch(
        TelemetryEnvelope(
            kind="trace",
            payload=TraceRecord(
                trace_id=f"trace-group-{index}",
                request_id=f"request-group-{index}",
                session_id=f"session-group-{index}",
                turn_id=f"turn-group-{index}",
                chain_id=f"chain-{index}",
                started_at=started_at - timedelta(seconds=index),
                completed_at=started_at - timedelta(seconds=index),
            ),
        )
        for index in range(2)
    )

    try:
        monkeypatch.setattr(sqlite_repository_module, "MAX_ANALYSIS_ROWS", 1)
        page = repository.trace_groups(days=1)
        assert len(page["items"]) == 1
        assert page["analysis"] == {"truncated": True, "row_limit": 1}
    finally:
        repository.close()
        for suffix in ("", "-wal", "-shm"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)


def test_sqlite_top_level_percentiles_remain_exact_when_analysis_rows_are_truncated(monkeypatch) -> None:
    path = Path.cwd() / f".audit-percentiles-{uuid4().hex}.sqlite3"
    repository = TelemetryRepository(path)
    now = datetime.now(timezone.utc)
    repository.write_batch(
        TelemetryEnvelope(
            kind="trace",
            payload=TraceRecord(
                trace_id=f"trace-percentile-{index}",
                request_id=f"request-percentile-{index}",
                session_id="session-percentile",
                turn_id=f"turn-percentile-{index}",
                started_at=now - timedelta(seconds=2 - index),
                completed_at=now - timedelta(seconds=2 - index),
                duration_ms=100 + index * 100,
                ttft_ms=10 + index * 10,
                status="ok",
            ),
        )
        for index in range(2)
    )

    try:
        monkeypatch.setattr(sqlite_repository_module, "MAX_ANALYSIS_ROWS", 1)
        overview = repository.overview(days=1)
        assert overview["latency_ms"]["p95"] == 200
        assert overview["ttft_ms"]["p95"] == 20
        assert overview["analysis"]["truncated"] is True
    finally:
        repository.close()
        for suffix in ("", "-wal", "-shm"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)


def test_manual_prune_cannot_shortcut_configured_retention() -> None:
    assert retention_module.enforce_manual_retention(
        configured_trace_days=30,
        configured_event_days=30,
        requested_trace_days=1,
        requested_event_days=1,
    ) == (30, 30)
    assert retention_module.enforce_manual_retention(
        configured_trace_days=30,
        configured_event_days=30,
        requested_trace_days=90,
        requested_event_days=7,
    ) == (90, 30)


def test_mysql_gateway_reset_has_a_global_runtime_cleanup_boundary() -> None:
    class Result:
        rowcount = 1

    class Connection:
        def __init__(self):
            self.statements: list[str] = []

        def execute(self, statement, _params=None):
            self.statements.append(str(statement))
            return Result()

    class Begin:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self.connection

        def __exit__(self, *_args):
            return None

    class Engine:
        def __init__(self):
            self.connection = Connection()

        def begin(self):
            return Begin(self.connection)

    repository = object.__new__(MySQLGatewayRepository)
    repository._engine = Engine()

    result = repository.clear_learning_sessions()

    assert result["gateway_turns"] == 1
    sql = " ".join(repository._engine.connection.statements).lower()
    assert "nlp_turn_events" in sql
    assert "nlp_exercise_attempts" in sql
    assert "nlp_guided_sessions" in sql
    assert "nlp_conversations" in sql
    assert "nlp_users" not in sql
    assert "nlp_user_preferences" not in sql


def test_usage_dimensions_have_a_bounded_database_page() -> None:
    class Result:
        def mappings(self):
            return self

        def all(self):
            return [
                {
                    "provider": "openai",
                    "events": 4,
                    "priced_events": 4,
                    "unpriced_events": 0,
                    "priced_credits_micro": 20,
                    **{field: 1 for field in (
                        "input_tokens", "cached_input_tokens", "cache_miss_input_tokens", "cache_write_input_tokens",
                        "output_tokens", "reasoning_output_tokens", "total_tokens",
                    )},
                }
            ]

        def scalar_one(self):
            return 2

    class Connection:
        def __init__(self):
            self.statements = []

        def execute(self, statement):
            self.statements.append(statement)
            return Result()

    class Connect:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self.connection

        def __exit__(self, *_args):
            return None

    class Engine:
        def __init__(self):
            self.connection = Connection()

        def connect(self):
            return Connect(self.connection)

    service = object.__new__(UsageReadService)
    service._engine = Engine()

    page = service.system_dimension_page(dimension="providers", limit=1, offset=0)

    assert page["total"] == 2
    assert page["has_more"] is True
    assert len(page["items"]) == 1
    assert any("LIMIT" in str(statement).upper() for statement in service._engine.connection.statements)


def test_usage_trend_aggregates_five_minute_buckets_in_sql() -> None:
    class Result:
        def mappings(self):
            return self

        def all(self):
            return [
                {
                    "bucket_epoch": 1_756_000_000,
                    "events": 4,
                    "priced_events": 4,
                    "unpriced_events": 0,
                    "priced_credits_micro": 20,
                    **{field: 1 for field in (
                        "input_tokens", "cached_input_tokens", "cache_miss_input_tokens", "cache_write_input_tokens",
                        "output_tokens", "reasoning_output_tokens", "total_tokens",
                    )},
                }
            ]

    class Connection:
        class Dialect:
            name = "mysql"

        dialect = Dialect()

        def __init__(self):
            self.statements = []

        def execute(self, statement):
            self.statements.append(statement)
            return Result()

    class Connect:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self.connection

        def __exit__(self, *_args):
            return None

    class Engine:
        def __init__(self):
            self.connection = Connection()

        def connect(self):
            return Connect(self.connection)

    service = object.__new__(UsageReadService)
    service._engine = Engine()
    end = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)

    result = service.system_trend(window_minutes=120, bucket_minutes=5, now=end)

    assert result["events"] == 4
    assert result["tokens"]["total_tokens"] == 1
    assert result["breakdown"][0]["purpose"] == "all"
    assert "GROUP BY" in str(service._engine.connection.statements[0]).upper()


def test_mysql_trace_rows_can_be_requested_in_chronological_order() -> None:
    class Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class Connection:
        def __init__(self):
            self.statement = None

        def execute(self, statement):
            self.statement = statement
            return Result()

    class Connect:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self.connection

        def __exit__(self, *_args):
            return None

    class Engine:
        def __init__(self):
            self.connection = Connection()

        def connect(self):
            return Connect(self.connection)

    repository = object.__new__(MySQLTelemetryRepository)
    repository._engine = Engine()
    repository._rows("span", trace_id="trace-1", limit=100, ascending=True)

    assert "ASC" in str(repository._engine.connection.statement).upper()


def test_mysql_health_reports_observability_storage_bytes() -> None:
    class Connection:
        def scalar(self, _statement):
            return 8192

    class Connect:
        def __enter__(self):
            return Connection()

        def __exit__(self, *_args):
            return None

    class Engine:
        def connect(self):
            return Connect()

    repository = object.__new__(MySQLTelemetryRepository)
    repository._engine = Engine()

    assert repository.health()["database_bytes"] == 8192


@pytest.mark.asyncio
async def test_sandbox_inventory_queries_return_bounded_pages() -> None:
    class Runtime:
        id = "runtime-1"
        state = "ready_unbound"
        node_id = "node-1"
        runtime_kind = "docker"
        resource_profile_id = "python-base"
        external_runtime_id = None
        failure_reason = None
        updated_at = None

    class Execution:
        id = "execution-1"
        owner_user_id = "user-1"
        environment_id = "environment-1"
        runtime_instance_id = "runtime-1"
        status = "failed"
        generation = 1
        started_at = None
        completed_at = None
        exit_reason = "test"
        trace_id = None
        span_id = None
        parent_span_id = None

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def scalar_one(self):
            return 25

        def scalars(self):
            return self

        def all(self):
            return self.rows

    class Database:
        def __init__(self):
            self.statements = []
            self.calls = 0

        async def execute(self, statement):
            self.statements.append(statement)
            self.calls += 1
            return Result([Runtime()] if self.calls == 2 else [Execution()])

    database = Database()
    page = await list_runtimes(database, limit=12, offset=12)

    assert page["total"] == 25
    assert page["offset"] == 12
    assert page["has_more"] is True
    assert len(page["items"]) == 1
    assert any("LIMIT" in str(statement).upper() and "OFFSET" in str(statement).upper() for statement in database.statements)

    execution_page = await list_executions(database, status_filter="failed", limit=12, offset=24)
    assert execution_page["offset"] == 24
    assert execution_page["has_more"] is False


def test_monitor_does_not_expose_docs_or_runtime_details_on_public_health() -> None:
    from server.monitor.app import create_monitor_app
    from server.web.auth import SameOriginSessionAuth

    class Runtime:
        def health(self):
            return {"database": "secret-path", "traces": 3}

        async def close(self):
            return None

    app = create_monitor_app(
        runtime=Runtime(),
        auth=SameOriginSessionAuth(
            secret="audit-health-secret",
            cookie_name="audit_health",
            allowed_origins=["http://testserver"],
        ),
        allowed_hosts=["testserver"],
        usage_reader=object(),
    )

    paths = {route.path for route in app.routes if hasattr(route, "path")}
    ready = next(route for route in app.routes if getattr(route, "path", None) == "/health/ready")

    assert "/api/docs" not in paths
    assert "/api/openapi.json" not in paths
    assert asyncio.run(ready.endpoint()) == {"status": "ready", "plane": "observability"}
