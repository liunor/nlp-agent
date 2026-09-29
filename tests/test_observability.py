import asyncio
import sqlite3
from datetime import datetime, timezone
import pytest

from core.observability.context import TelemetryContext, bind_telemetry_context
from core.observability.models import SpanKind, SpanStatus, TokenUsage
from core.observability.runtime import TelemetryRuntime, usage_from_metadata
from core.observability.service import ObservabilityService
from core.observability.summary import (
    build_dependency_health,
    build_error_analysis,
    build_telemetry_overview,
)
from core.observability.traces import build_trace_group_page, derive_trace_identity
from core.identity import AuthenticatedPrincipal
from core.identity import AccessDeniedError


ADMIN = AuthenticatedPrincipal.system_admin()


def test_usage_from_metadata_preserves_provider_reported_kv_cache_tokens():
    usage = usage_from_metadata({
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "cached_tokens": 0,
        "prompt_cache_hit_tokens": 75,
        "prompt_cache_miss_tokens": 25,
    })

    assert usage.input_tokens == 100
    assert usage.cached_tokens == 75
    assert usage.cache_miss_tokens == 25
    assert usage.total_tokens == 120


def test_trace_identity_prefers_explicit_chain_labels_and_keeps_entrypoint():
    identity = derive_trace_identity(
        session_id="session-1",
        turn_id="turn-1",
        source="user",
        attributes={
            "chain_id": "workflow-42",
            "chain_name": "文档处理",
            "entrypoint": "/api/v1/chat",
        },
    )

    assert identity == {
        "chain_id": "workflow-42",
        "chain_name": "文档处理",
        "entrypoint": "/api/v1/chat",
    }


def test_trace_identity_groups_unlabelled_turns_by_session_and_channel():
    identity = derive_trace_identity(
        session_id="session-1",
        turn_id="turn-1",
        source="user",
        channel="web",
    )

    assert identity == {
        "chain_id": "session:session-1",
        "chain_name": "用户会话",
        "entrypoint": "web:agent.turn",
    }


def test_trace_group_page_aggregates_by_chain_and_returns_only_one_page():
    rows = [
        {
            "trace_id": "trace-1",
            "chain_id": "workflow-42",
            "chain_name": "文档处理",
            "entrypoint": "/api/v1/chat",
            "user_id": "alice",
            "workspace_id": "workspace-a",
            "started_at": "2026-09-05T10:00:00+00:00",
            "completed_at": "2026-09-05T10:00:03+00:00",
            "duration_ms": 3000,
            "status": "error",
            "error_kind": "TimeoutError",
            "total_tokens": 120,
        },
        {
            "trace_id": "trace-2",
            "chain_id": "workflow-42",
            "chain_name": "文档处理",
            "entrypoint": "/api/v1/chat",
            "user_id": "alice",
            "workspace_id": "workspace-a",
            "started_at": "2026-09-05T10:00:04+00:00",
            "completed_at": "2026-09-05T10:00:05+00:00",
            "duration_ms": 1000,
            "status": "ok",
            "total_tokens": 80,
        },
        {
            "trace_id": "trace-3",
            "chain_id": "workflow-99",
            "chain_name": "搜索",
            "entrypoint": "/api/v1/search",
            "user_id": "bob",
            "workspace_id": "workspace-b",
            "started_at": "2026-09-05T09:00:00+00:00",
            "completed_at": "2026-09-05T09:00:01+00:00",
            "duration_ms": 1000,
            "status": "ok",
            "total_tokens": 30,
        },
    ]

    page = build_trace_group_page(rows, limit=1, offset=0)

    assert page["total"] == 2
    assert page["has_more"] is True
    assert len(page["items"]) == 1
    assert page["items"][0]["chain_id"] == "workflow-42"
    assert page["items"][0]["trace_count"] == 2
    assert page["items"][0]["error_count"] == 1
    assert page["items"][0]["user_ids"] == ["alice"]
    assert page["items"][0]["sample_trace_id"] == "trace-1"

    filtered = build_trace_group_page(
        rows,
        spans=[
            {
                "trace_id": "trace-1",
                "kind": "model",
                "name": "gateway.model",
                "status": "error",
                "error_kind": "TimeoutError",
            }
        ],
        query="TimeoutError|model|gateway.model",
    )
    assert filtered["total"] == 1
    assert filtered["items"][0]["chain_id"] == "workflow-42"

    trace_only = build_trace_group_page(
        rows,
        query="TimeoutError|trace|/api/v1/chat",
        focus="errors",
    )
    assert trace_only["total"] == 1
    assert trace_only["items"][0]["chain_id"] == "workflow-42"


def test_trace_group_page_promotes_failed_internal_span_even_when_trace_is_ok():
    rows = [{
        "trace_id": "trace-ok",
        "chain_id": "workflow-42",
        "chain_name": "文档处理",
        "entrypoint": "/api/v1/chat",
        "user_id": "alice",
        "workspace_id": "workspace-a",
        "started_at": "2026-09-05T10:00:00+00:00",
        "completed_at": "2026-09-05T10:00:03+00:00",
        "duration_ms": 3000,
        "status": "ok",
        "total_tokens": 120,
    }]
    spans = [{
        "trace_id": "trace-ok",
        "span_id": "span-model",
        "status": "timeout",
        "error_kind": "ProviderTimeout",
        "attributes": {"provider": "openai", "model": "gpt-5.4"},
    }]

    page = build_trace_group_page(rows, spans=spans)

    assert page["items"][0]["error_count"] == 1
    assert page["items"][0]["failed_span_count"] == 1
    assert page["items"][0]["error_kinds"] == ["ProviderTimeout"]
    assert page["items"][0]["provider_models"] == ["openai / gpt-5.4"]


def test_trace_group_page_slow_focus_uses_single_trace_latency_not_aggregate():
    rows = [
        {
            "trace_id": "trace-1",
            "chain_id": "workflow-42",
            "started_at": "2026-09-05T10:00:00+00:00",
            "completed_at": "2026-09-05T10:00:00.600000+00:00",
            "duration_ms": 600,
            "status": "ok",
        },
        {
            "trace_id": "trace-2",
            "chain_id": "workflow-42",
            "started_at": "2026-09-05T10:01:00+00:00",
            "completed_at": "2026-09-05T10:01:00.600000+00:00",
            "duration_ms": 600,
            "status": "ok",
        },
    ]

    page = build_trace_group_page(rows, focus="slow")

    assert page["total"] == 0


def test_sqlite_repository_migrates_trace_identity_columns_before_indexing(tmp_path):
    database = tmp_path / "legacy-telemetry.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE traces (
                trace_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                status TEXT NOT NULL
            );
            """
        )

    from core.observability.repository import TelemetryRepository

    TelemetryRepository(database)

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(traces)")}
    assert {"chain_id", "chain_name", "entrypoint"}.issubset(columns)


def test_telemetry_overview_surfaces_all_users_and_operational_dimensions():
    now = "2026-09-04T10:00:00+00:00"
    overview = build_telemetry_overview(
        traces=[
            {
                "trace_id": "trace-alice",
                "user_id": "alice",
                "workspace_id": "workspace-a",
                "session_id": "session-a",
                "channel": "web",
                "source": "user",
                "started_at": now,
                "completed_at": now,
                "duration_ms": 120,
                "ttft_ms": 40,
                "status": "ok",
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            },
            {
                "trace_id": "trace-bob",
                "user_id": "bob",
                "workspace_id": "workspace-b",
                "session_id": "session-b",
                "channel": "api",
                "source": "worker_resume",
                "started_at": now,
                "completed_at": now,
                "duration_ms": 300,
                "ttft_ms": 100,
                "status": "timeout",
                "input_tokens": 20,
                "output_tokens": 2,
                "total_tokens": 22,
            },
        ],
        spans=[
            {
                "trace_id": "trace-alice",
                "kind": "model",
                "name": "coordinator.model",
                "status": "ok",
                "started_at": now,
                "completed_at": now,
                "duration_ms": 80,
                "attributes": {"provider": "deepseek", "provider_model": "deepseek-chat"},
                "total_tokens": 15,
            },
            {
                "trace_id": "trace-bob",
                "kind": "tool",
                "name": "web.search",
                "status": "timeout",
                "started_at": now,
                "completed_at": now,
                "duration_ms": 200,
                "error_kind": "TimeoutError",
                "attributes": {"tool_provider": "web"},
                "total_tokens": 0,
            },
        ],
        events=[
            {"timestamp": now, "level": "error", "name": "tool.timeout"},
            {"timestamp": now, "level": "info", "name": "agent.completed"},
        ],
        days=30,
    )

    assert overview["active_users"] == 2
    assert overview["active_workspaces"] == 2
    assert overview["active_sessions"] == 2
    assert overview["successes"] == 1
    assert overview["errors"] == 1
    assert overview["latency_ms"] == {"p50": 120, "p90": 300, "p95": 300, "p99": 300}
    assert overview["tags"]["channels"] == [
        {"value": "api", "requests": 1},
        {"value": "web", "requests": 1},
    ]
    assert any(row["name"] == "web.search" for row in overview["component_spans"])
    assert overview["models"][0]["provider_model"] == "deepseek-chat"
    assert overview["error_groups"][0]["sample_trace_id"] == "trace-bob"
    assert overview["events_by_level"] == {"error": 1, "info": 1}
    assert overview["top_users"][0]["user_id"] == "alice"


def test_telemetry_overview_exposes_langsmith_style_latency_percentiles():
    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    rows = [
        {
            "trace_id": f"trace-{duration}",
            "session_id": f"session-{duration}",
            "user_id": "alice",
            "workspace_id": "workspace-a",
            "started_at": now.isoformat(),
            "completed_at": now.isoformat(),
            "duration_ms": duration,
            "ttft_ms": duration,
            "status": "ok",
        }
        for duration in range(1, 101)
    ]

    overview = build_telemetry_overview(rows, [], [], days=30, now=now)

    assert overview["latency_ms"] == {"p50": 50, "p90": 90, "p95": 95, "p99": 99}
    assert overview["ttft_ms"] == {"p50": 50, "p90": 90, "p95": 95, "p99": 99}


def test_dependency_health_aggregates_all_users_and_keeps_a_five_minute_window():
    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    traces = [
        {
            "trace_id": "trace-alice",
            "user_id": "alice",
            "workspace_id": "workspace-a",
            "started_at": "2026-09-05T11:55:00+00:00",
            "completed_at": "2026-09-05T11:55:04+00:00",
            "duration_ms": 4000,
            "ttft_ms": 800,
            "status": "ok",
            "total_tokens": 100,
        },
        {
            "trace_id": "trace-bob",
            "user_id": "bob",
            "workspace_id": "workspace-b",
            "started_at": "2026-09-05T11:55:10+00:00",
            "completed_at": "2026-09-05T11:55:12+00:00",
            "duration_ms": 2000,
            "ttft_ms": 400,
            "status": "error",
            "total_tokens": 60,
        },
    ]
    spans = [
        {
            "trace_id": "trace-alice",
            "span_id": "model-1",
            "kind": "model",
            "name": "gateway.model",
            "started_at": "2026-09-05T11:55:00+00:00",
            "completed_at": "2026-09-05T11:55:03+00:00",
            "duration_ms": 3000,
            "status": "ok",
            "attempt": 1,
            "total_tokens": 100,
            "attributes": {"provider": "openai", "provider_model": "gpt-5.4"},
        },
        {
            "trace_id": "trace-bob",
            "span_id": "model-2",
            "kind": "model",
            "name": "gateway.model",
            "started_at": "2026-09-05T11:55:10+00:00",
            "completed_at": "2026-09-05T11:55:12+00:00",
            "duration_ms": 2000,
            "status": "timeout",
            "attempt": 2,
            "error_kind": "TimeoutError",
            "total_tokens": 60,
            "attributes": {"provider": "openai", "provider_model": "gpt-5.4"},
        },
    ]

    result = build_dependency_health(traces, spans, days=30, now=now)

    assert result["scope"] == "system"
    assert result["summary"]["active_users"] == 2
    assert result["summary"]["requests"] == 2
    assert result["summary"]["component_calls"] == 2
    assert result["models"][0]["provider_model"] == "gpt-5.4"
    assert result["models"][0]["users"] == 2
    assert result["models"][0]["latency_ms"]["p95"] == 3000
    assert len(result["trend"]) == 24
    assert result["trend"][-1]["requests"] == 2
    assert result["trend"][-1]["component_errors"] == 1


def test_error_analysis_groups_fingerprints_reports_impact_and_recovery_without_raw_messages():
    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    traces = [
        {
            "trace_id": "trace-error",
            "user_id": "alice",
            "workspace_id": "workspace-a",
            "started_at": "2026-09-05T11:40:00+00:00",
            "completed_at": "2026-09-05T11:40:05+00:00",
            "duration_ms": 5000,
            "status": "error",
            "error_kind": "TimeoutError",
        },
        {
            "trace_id": "trace-recovered",
            "user_id": "bob",
            "workspace_id": "workspace-b",
            "started_at": "2026-09-05T11:50:00+00:00",
            "completed_at": "2026-09-05T11:50:02+00:00",
            "duration_ms": 2000,
            "status": "ok",
        },
    ]
    spans = [
        {
            "trace_id": "trace-error",
            "span_id": "span-error",
            "kind": "model",
            "name": "gateway.model",
            "started_at": "2026-09-05T11:40:00+00:00",
            "completed_at": "2026-09-05T11:40:05+00:00",
            "duration_ms": 5000,
            "status": "timeout",
            "error_kind": "TimeoutError",
            "error_message": "secret prompt content must never leave the detail view",
            "attributes": {"provider": "openai", "provider_model": "gpt-5.4"},
        },
        {
            "trace_id": "trace-recovered",
            "span_id": "span-recovered",
            "kind": "model",
            "name": "gateway.model",
            "started_at": "2026-09-05T11:50:00+00:00",
            "completed_at": "2026-09-05T11:50:02+00:00",
            "duration_ms": 2000,
            "status": "ok",
            "attributes": {"provider": "openai", "provider_model": "gpt-5.4"},
        },
    ]

    result = build_error_analysis(traces, spans, days=30, now=now)
    item = next(row for row in result["items"] if row["kind"] == "model")

    assert item["fingerprint"] == "TimeoutError|model|gateway.model"
    assert item["count"] == 1
    assert item["trace_count"] == 1
    assert item["affected_users"] == 1
    assert item["provider_models"] == ["openai / gpt-5.4"]
    assert item["recovery_status"] == "recovered"
    assert "error_message" not in item
    assert result["summary"]["affected_requests"] == 1
    assert result["summary"]["recovered_groups"] == 1


def test_error_analysis_does_not_recover_an_older_fingerprint_after_a_newer_failure():
    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    traces = [
        {
            "trace_id": trace_id,
            "user_id": "alice",
            "workspace_id": "workspace-a",
            "started_at": timestamp,
            "completed_at": timestamp,
            "status": "ok",
        }
        for trace_id, timestamp in (
            ("trace-timeout", "2026-09-05T11:40:00+00:00"),
            ("trace-auth", "2026-09-05T11:45:00+00:00"),
            ("trace-success", "2026-09-05T11:50:00+00:00"),
        )
    ]
    spans = [
        {
            "trace_id": "trace-timeout",
            "span_id": "span-timeout",
            "kind": "model",
            "name": "gateway.model",
            "started_at": "2026-09-05T11:40:00+00:00",
            "completed_at": "2026-09-05T11:40:01+00:00",
            "duration_ms": 1000,
            "status": "timeout",
            "error_kind": "TimeoutError",
        },
        {
            "trace_id": "trace-auth",
            "span_id": "span-auth",
            "kind": "model",
            "name": "gateway.model",
            "started_at": "2026-09-05T11:45:00+00:00",
            "completed_at": "2026-09-05T11:45:01+00:00",
            "duration_ms": 1000,
            "status": "denied",
            "error_kind": "AuthError",
        },
        {
            "trace_id": "trace-success",
            "span_id": "span-success",
            "kind": "model",
            "name": "gateway.model",
            "started_at": "2026-09-05T11:50:00+00:00",
            "completed_at": "2026-09-05T11:50:01+00:00",
            "duration_ms": 1000,
            "status": "ok",
        },
    ]

    result = build_error_analysis(traces, spans, days=30, now=now)
    statuses = {row["error_kind"]: row["recovery_status"] for row in result["items"]}

    assert statuses == {"TimeoutError": "stale", "AuthError": "recovered"}


def test_operational_trend_uses_a_real_sliding_window_and_counts_denied_requests():
    now = datetime(2026, 9, 5, 12, 3, 7, tzinfo=timezone.utc)
    traces = [{
        "trace_id": "trace-denied",
        "user_id": "alice",
        "workspace_id": "workspace-a",
        "started_at": "2026-09-05T12:02:00+00:00",
        "completed_at": "2026-09-05T12:02:01+00:00",
        "status": "denied",
    }]

    result = build_dependency_health(traces, [], days=30, now=now)

    assert result["to"] == now.isoformat()
    assert result["trend"][-1]["period_end"] == now.isoformat()
    assert result["trend"][-1]["errors"] == 1


@pytest.mark.parametrize(
    ("durations", "expected"),
    [
        ((100,), {"p50": 100, "p90": 100, "p95": 100, "p99": 100}),
        ((100, 10_000), {"p50": 100, "p90": 10_000, "p95": 10_000, "p99": 10_000}),
        ((100, 500, 10_000), {"p50": 500, "p90": 10_000, "p95": 10_000, "p99": 10_000}),
    ],
)
def test_telemetry_percentiles_keep_small_sample_tail_latency_visible(durations, expected):
    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    rows = [
        {
            "trace_id": f"trace-{duration}",
            "session_id": f"session-{duration}",
            "user_id": "alice",
            "workspace_id": "workspace-a",
            "started_at": now.isoformat(),
            "completed_at": now.isoformat(),
            "duration_ms": duration,
            "ttft_ms": duration,
            "status": "ok",
        }
        for duration in durations
    ]

    overview = build_telemetry_overview(rows, [], [], days=30, now=now)

    assert overview["latency_ms"] == expected
    assert overview["ttft_ms"] == expected


async def test_trace_span_usage_and_queries(tmp_path):
    runtime = TelemetryRuntime(tmp_path / "telemetry.sqlite3", flush_interval_s=0.01)
    service = ObservabilityService(runtime)
    context = TelemetryContext.create(
        session_id="session-1", turn_id="turn-1", workspace_id="workspace-1"
    )

    runtime.start_trace(context)
    with bind_telemetry_context(context):
        async with runtime.span(
            SpanKind.MODEL, "coordinator.model", attributes={"model": "test-model"}
        ) as span:
            span.set_usage(TokenUsage(
                input_tokens=12, output_tokens=3, total_tokens=15, source="provider"
            ))
            runtime.event("model.response", payload={"finish_reason": "stop"})
        runtime.mark_ttft()
    runtime.complete_trace(context)
    await runtime.flush()

    overview = await service.overview(ADMIN)
    assert overview["requests"] == 1
    assert overview["successes"] == 1
    assert overview["tokens"]["total_tokens"] == 15

    traces = await service.traces(ADMIN, session_id="session-1")
    assert traces[0]["trace_id"] == context.trace_id
    detail = await service.trace(ADMIN, context.trace_id)
    assert detail is not None
    assert detail["spans"][0]["parent_span_id"] == context.span_id
    assert detail["events"][0]["name"] == "model.response"
    usage = await service.usage(ADMIN)
    assert usage[0]["total_tokens"] == 15
    await runtime.close()


async def test_error_aggregation_and_live_subscription(tmp_path):
    runtime = TelemetryRuntime(tmp_path / "telemetry.sqlite3", flush_interval_s=0.01)
    service = ObservabilityService(runtime)
    context = TelemetryContext.create(session_id="session-2", turn_id="turn-2")
    queue = service.subscribe(ADMIN)

    runtime.start_trace(context)
    with bind_telemetry_context(context):
        async with runtime.span(SpanKind.TOOL, "tool.search") as span:
            span.set_status(
                SpanStatus.TIMEOUT, error_kind="timeout", error_message="request timed out"
            )
    runtime.complete_trace(context, status=SpanStatus.ERROR)
    await runtime.flush()

    live = await asyncio.wait_for(queue.get(), timeout=1)
    assert live["kind"] == "trace"
    errors = await service.errors(ADMIN)
    assert errors[0]["error_kind"] == "timeout"
    assert errors[0]["count"] == 1
    service.unsubscribe(queue)
    await runtime.close()


async def test_telemetry_repository_clear_removes_traces_events_and_daily_usage(tmp_path):
    runtime = TelemetryRuntime(tmp_path / "telemetry.sqlite3", flush_interval_s=0.01)
    context = TelemetryContext.create(session_id="reset-session", turn_id="reset-turn")
    runtime.start_trace(context)
    with bind_telemetry_context(context):
        async with runtime.span(SpanKind.MODEL, "reset.model"):
            runtime.event("reset.event")
    runtime.complete_trace(context)
    await runtime.flush()

    assert runtime.repository.clear() == {"traces": 1, "spans": 1, "events": 1, "daily_metrics": 1}
    assert runtime.repository.health()["traces"] == 0
    assert runtime.repository.health()["spans"] == 0
    assert runtime.repository.health()["events"] == 0
    assert runtime.repository.usage() == []
    await runtime.close()


async def test_observability_queries_are_scoped_to_principal(tmp_path):
    runtime = TelemetryRuntime(tmp_path / "telemetry.sqlite3", flush_interval_s=0.01)
    service = ObservabilityService(runtime)
    context = TelemetryContext.create(
        session_id="private-session",
        turn_id="turn-private",
        workspace_id="w1",
        user_id="alice",
    )
    runtime.start_trace(context)
    runtime.complete_trace(context)
    await runtime.flush()

    alice = AuthenticatedPrincipal(user_id="alice", workspace_ids=frozenset({"w1"}))
    bob = AuthenticatedPrincipal(user_id="bob", workspace_ids=frozenset({"w1"}))
    assert len(await service.traces(alice)) == 1
    assert await service.traces(bob) == []
    with pytest.raises(AccessDeniedError):
        await service.trace(bob, context.trace_id)
    await runtime.close()


def test_runtime_moves_pending_events_to_a_replacement_event_loop(tmp_path):
    runtime = TelemetryRuntime(tmp_path / "telemetry.sqlite3", flush_interval_s=60)

    async def emit_without_flush():
        runtime.event("loop.one")

    asyncio.run(emit_without_flush())
    asyncio.run(runtime.flush())

    assert runtime.repository.health()["events"] == 1
    asyncio.run(runtime.close())
