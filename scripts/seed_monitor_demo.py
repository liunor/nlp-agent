"""Seed synthetic monitoring data for local dashboard review.

The script is deliberately scoped to localhost MySQL and to rows carrying the
``monitor-demo-v1`` marker.  It never touches real users, prompts, or secrets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine, delete, select
from sqlalchemy.dialects.mysql import insert

from configs.settings import settings
from core.observability.models import (
    SpanKind,
    SpanRecord,
    SpanStatus,
    TelemetryEnvelope,
    TelemetryEvent,
    TokenUsage,
    TraceRecord,
)
from server.infrastructure.mysql.models import (
    ObservabilityRecordModel,
    SandboxArtifactModel,
    SandboxEnvironmentModel,
    SandboxExecutionModel,
    SandboxLeaseModel,
    SandboxRuntimeInstanceModel,
    UserModel,
    WorkspaceMemberModel,
    WorkspaceModel,
)
from server.quota.models import UsageEventModel


MARKER = "monitor-demo-v1"
PREFIX = f"{MARKER}-"
SANDBOX_ENVIRONMENT_PROFILE = f"{MARKER}:python-base"
SANDBOX_WORKSPACE_SLUG = f"{MARKER}-workspace"
SANDBOX_HISTORY_COUNT = 60
SANDBOX_USERS = (
    ("alice", "Alice"),
    ("bob", "Bob"),
    ("charlie", "Charlie"),
    ("diana", "Diana"),
)


def is_local_endpoint(url: str) -> bool:
    """Return whether a DSN points at an exact loopback hostname.

    The demo seed performs scoped deletes, so accepting a remote endpoint based
    on a substring such as ``"localhost" in url`` would be an unsafe default.
    """
    try:
        hostname = (urlsplit(url).hostname or "").casefold().rstrip(".")
    except ValueError:
        return False
    return hostname in {"localhost", "127.0.0.1", "::1"}


def _uuid(kind: str, index: int = 0) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"nlp-agent:{MARKER}:{kind}:{index}"))


def _id(kind: str, index: int) -> str:
    return f"{PREFIX}{kind}-{index:03d}-{uuid.uuid4().hex[:8]}"


def _utc(day_offset: int, minute: int) -> datetime:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return now - timedelta(days=day_offset, minutes=minute)


def _usage(index: int) -> TokenUsage:
    input_tokens = 700 + index * 41
    output_tokens = 180 + index * 17
    cached = 120 if index % 3 == 0 else 0
    reasoning = 90 if index % 4 == 0 else 0
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached,
        cache_miss_tokens=max(0, input_tokens - cached),
        reasoning_tokens=reasoning,
        total_tokens=input_tokens + output_tokens + reasoning,
        source="provider",
    )


def _build_observability(count: int = 96) -> tuple[list[TelemetryEnvelope], list[dict]]:
    envelopes: list[TelemetryEnvelope] = []
    usage_rows: list[dict] = []
    users = ["demo-user-alice", "demo-user-bob", "demo-user-charlie", "demo-user-diana"]
    workspaces = ["demo-workspace-lab", "demo-workspace-prod"]
    models = [
        ("openai", "gpt-5.4-mini"),
        ("openai", "gpt-5.4"),
        ("anthropic", "claude-sonnet-4"),
    ]
    channels = ["web", "api", "cli", "sandbox"]

    for index in range(count):
        user = users[index % len(users)]
        workspace = workspaces[index % len(workspaces)]
        provider, model_name = models[index % len(models)]
        is_timeout = index % 19 == 7
        is_error = not is_timeout and index % 17 in {4, 13}
        has_retry = is_timeout or is_error
        status = SpanStatus.TIMEOUT if is_timeout else SpanStatus.ERROR if is_error else SpanStatus.OK
        trace_id = _id("trace", index)
        session_id = f"{PREFIX}session-{index % 8:02d}"
        turn_id = f"{PREFIX}turn-{index:03d}"
        started = _utc(index % 8, index * 7)
        duration = 280 + index * 33
        usage = _usage(index)
        attributes = {
            "demo_seed_id": MARKER,
            "model": model_name,
            "provider": provider,
            "request_type": "chat_completion" if index % 3 else "tool_augmented_chat",
            "retry_count": 1 if has_retry else 0,
        }
        trace = TraceRecord(
            trace_id=trace_id,
            request_id=_id("request", index),
            session_id=session_id,
            turn_id=turn_id,
            workspace_id=workspace,
            user_id=user,
            channel=channels[index % len(channels)],
            source="demo",
            started_at=started,
            completed_at=started + timedelta(milliseconds=duration),
            duration_ms=duration,
            ttft_ms=80 + index * 9,
            status=status,
            usage=usage,
            error_kind="provider_timeout" if status == SpanStatus.TIMEOUT else "upstream_5xx" if status == SpanStatus.ERROR else None,
            error_message="Synthetic demo failure" if status != SpanStatus.OK else None,
            attributes=attributes,
        )
        envelopes.append(TelemetryEnvelope(kind="trace", payload=trace))

        root_span_id = _id("span", index * 3)
        model_span_id = _id("span", index * 3 + 1)
        tool_span_id = _id("span", index * 3 + 2)
        root = SpanRecord(
            trace_id=trace_id, span_id=root_span_id, session_id=session_id,
            turn_id=turn_id, kind=SpanKind.GATEWAY, name="gateway.request",
            started_at=started, completed_at=started + timedelta(milliseconds=duration),
            duration_ms=duration, status=status, attributes={"demo_seed_id": MARKER},
        )
        model = SpanRecord(
            trace_id=trace_id, span_id=model_span_id, parent_span_id=root_span_id,
            session_id=session_id, turn_id=turn_id, kind=SpanKind.MODEL,
            name="model.invoke", started_at=started + timedelta(milliseconds=30),
            completed_at=started + timedelta(milliseconds=duration - 20),
            duration_ms=max(1, duration - 50), status=status, attempt=2 if has_retry else 1,
            usage=usage, error_kind=trace.error_kind, error_message=trace.error_message,
            attributes={"demo_seed_id": MARKER, "provider": provider, "model": model_name},
        )
        tool = SpanRecord(
            trace_id=trace_id, span_id=tool_span_id, parent_span_id=root_span_id,
            session_id=session_id, turn_id=turn_id, worker_id=f"{PREFIX}worker-{index % 3}",
            kind=SpanKind.TOOL, name="tool.search" if index % 2 else "tool.rerank",
            started_at=started + timedelta(milliseconds=10),
            completed_at=started + timedelta(milliseconds=duration // 3),
            duration_ms=max(1, duration // 3), status=SpanStatus.OK,
            attributes={"demo_seed_id": MARKER, "cache_hit": index % 2 == 0},
        )
        envelopes.extend(TelemetryEnvelope(kind="span", payload=item) for item in (root, model, tool))

        event_specs = [("request.completed", "info" if status == SpanStatus.OK else "error")]
        if has_retry:
            event_specs.insert(0, ("request.retry", "warning"))
        if index % 11 == 3:
            event_specs.append(("request.slow", "warning"))
        if index % 23 == 9:
            event_specs.append(("provider.rate_limited", "error"))
        if index % 5 == 0:
            event_specs.append(("queue.backpressure", "warning"))
        for event_index, (name, level) in enumerate(event_specs):
            event = TelemetryEvent(
                event_id=_id("event", index * 3 + event_index),
                timestamp=started + timedelta(milliseconds=duration), level=level,
                name=name, trace_id=trace_id, span_id=model_span_id,
                session_id=session_id, turn_id=turn_id,
                worker_id=f"{PREFIX}worker-{index % 3}",
                payload={"demo_seed_id": MARKER, "provider": provider, "model": model_name},
            )
            envelopes.append(TelemetryEnvelope(kind="event", payload=event))

        operation_id = f"{PREFIX}operation-{index:03d}"
        usage_rows.append({
            "id": str(uuid.uuid4()), "operation_id": operation_id,
            "reservation_id": f"{PREFIX}reservation-{index:03d}", "user_id": user,
            "workspace_id": workspace, "conversation_id": session_id,
            "turn_id": turn_id, "worker_id": f"{PREFIX}worker-{index % 3}",
            "parent_operation_id": None, "purpose": "chat", "provider": provider,
            "provider_model": model_name, "provider_response_id": f"{PREFIX}response-{index:03d}",
            "model_profile": "balanced", "preset": "demo", "route": "primary",
            "pricing_key": f"{provider}:{model_name}" if index % 6 else None,
            "attempt": 2 if has_retry else 1, "fallback_index": 1 if index % 29 == 19 else 0,
            "outcome_status": "timeout" if status == SpanStatus.TIMEOUT else "error" if status == SpanStatus.ERROR else "completed",
            "finish_reason": "stop" if status == SpanStatus.OK else None,
            "error_kind": trace.error_kind, "input_tokens": usage.input_tokens,
            "cached_input_tokens": usage.cached_tokens, "cache_write_input_tokens": 0,
            "output_tokens": usage.output_tokens, "reasoning_output_tokens": usage.reasoning_tokens,
            "total_tokens": usage.total_tokens, "usage_source": "provider",
            "usage_status": "priced" if index % 6 else "unpriced",
            "pricing_version": "demo-2026-01", "credits_micro": None if index % 6 else 0,
            "raw_usage_json": {"demo_seed_id": MARKER, "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
            "dedupe_key": f"{PREFIX}dedupe-{index:03d}", "idempotency_key": f"{PREFIX}idem-{index:03d}",
            "started_at": started, "occurred_at": started + timedelta(milliseconds=duration),
            "created_at": started + timedelta(milliseconds=duration), "archived_at": None, "archive_batch_id": None,
        })
    for index in range(max(3, count // 24)):
        timestamp = _utc(index, index * 11 + 3)
        envelopes.append(TelemetryEnvelope(kind="event", payload=TelemetryEvent(
            event_id=_id("system-event", index), timestamp=timestamp, level="warning",
            name="telemetry.backpressure", worker_id=f"{PREFIX}worker-{index % 3}",
            payload={
                "demo_seed_id": MARKER, "component": "telemetry", "operation": "event_ingest",
                "queue_size": 420 + index * 95, "queue_capacity": 5000,
            },
        )))
    return envelopes, usage_rows


def _build_sandbox_demo(
    count: int = 96,
    *,
    now: datetime | None = None,
    trace_ids: list[str] | None = None,
    span_ids: list[str] | None = None,
) -> tuple[list[dict], dict, list[dict], list[dict], list[dict], list[dict]]:
    """Build safe synthetic rows for the sandbox monitor.

    The generated principals are disabled demo users.  Sandbox records carry a
    stable marker in their IDs/metadata so ``--clear`` can remove only these
    rows.  No source code, stdout, prompt, or model response is generated.
    """
    if count < 1:
        raise ValueError("count must be at least 1")
    sampled_at = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    user_rows = [
        {
            "id": _uuid("sandbox-user", index),
            "username": f"{PREFIX}user-{slug}",
            "password_hash": "!monitor-demo-disabled",
            "display_name": f"监控模拟用户 {display_name}",
            "status": "disabled",
            "authorization_version": 1,
            "registration_source": "monitor_demo",
        }
        for index, (slug, display_name) in enumerate(SANDBOX_USERS)
    ]
    workspace = {
        "id": _uuid("sandbox-workspace"),
        "slug": SANDBOX_WORKSPACE_SLUG,
        "name": "监控模拟工作区",
        "status": "active",
    }
    environment_rows = [
        {
            "id": _uuid("sandbox-environment", index),
            "owner_user_id": user_rows[index]["id"],
            "resource_profile_id": SANDBOX_ENVIRONMENT_PROFILE,
            "profile_revision": 1,
            "status": "ready",
            "generation": 1,
            "active_runtime_id": None,
            "last_active_at": sampled_at - timedelta(seconds=index * 45),
        }
        for index in range(len(user_rows))
    ]

    runtime_states = (
        "ready_unbound", "ready_unbound", "ready_unbound", "ready_unbound",
        "assigned", "assigned", "creating", "creating", "claiming", "draining",
        "failed", "failed",
    )
    runtime_rows: list[dict] = []
    for index, state in enumerate(runtime_states):
        runtime_id = _uuid("sandbox-runtime", index)
        environment = environment_rows[index % len(environment_rows)]
        runtime_rows.append(
            {
                "id": runtime_id,
                "environment_id": environment["id"],
                "node_id": f"{PREFIX}node-{index % 3}",
                "runtime_kind": "docker",
                "external_runtime_id": f"{PREFIX}runtime-{index:03d}",
                "image_digest": f"sha256:{hashlib.sha256(f'{MARKER}:image:{index}'.encode()).hexdigest()}",
                "resource_profile_id": "python-base",
                "state": state,
                "generation": 1,
                "last_heartbeat_at": sampled_at - timedelta(seconds=index * 20)
                if state != "failed"
                else None,
                "failure_reason": "synthetic_runtime_boot_failure" if state == "failed" else None,
                "updated_at": sampled_at - timedelta(seconds=index * 20),
            }
        )
        if state in {"assigned", "claiming"}:
            environment["active_runtime_id"] = runtime_id

    execution_rows: list[dict] = []
    for index in range(count):
        runtime = runtime_rows[index % len(runtime_rows)]
        environment = environment_rows[index % len(environment_rows)]
        owner_user_id = environment["owner_user_id"]
        status = (
            "running" if index == 0
            else "timeout" if index % 17 == 8
            else "failed" if index % 11 == 4
            else "completed"
        )
        started_at = sampled_at - timedelta(seconds=15 * (index + 1))
        duration_ms = 420 + (index % 9) * 115
        completed_at = None if status == "running" else started_at + timedelta(milliseconds=duration_ms)
        trace_id = trace_ids[index % len(trace_ids)] if trace_ids else _uuid("sandbox-trace", index)
        span_id = span_ids[index % len(span_ids)] if span_ids else _uuid("sandbox-span", index)
        execution_rows.append(
            {
                "id": _uuid("sandbox-execution", index),
                "environment_id": environment["id"],
                "runtime_instance_id": runtime["id"],
                "owner_user_id": owner_user_id,
                "workspace_id": workspace["id"],
                "actor_type": "worker",
                "request_id": f"{PREFIX}sandbox-request-{index:03d}",
                "code_hash": hashlib.sha256(f"{MARKER}:code:{index}".encode()).hexdigest(),
                "status": status,
                "generation": 1,
                "started_at": started_at,
                "completed_at": completed_at,
                "exit_reason": (
                    "synthetic_execution_timeout" if status == "timeout"
                    else "synthetic_dependency_error" if status == "failed"
                    else None
                ),
                "resource_summary_json": {
                    "demo_seed_id": MARKER,
                    "duration_ms": duration_ms if completed_at else None,
                    "cpu_ms": 180 + (index % 7) * 37,
                    "memory_peak_mb": 96 + (index % 5) * 16,
                    "queue_ms": 30 + (index % 4) * 12,
                },
                "trace_id": trace_id,
                "span_id": span_id,
                "parent_span_id": None,
                "created_at": started_at,
            }
        )

    samples: list[dict] = []
    for index in range(SANDBOX_HISTORY_COUNT):
        ready = 2 + (index * 3) % 4
        creating = 1 if index % 9 in {0, 1} else 0
        target = 5
        samples.append(
            {
                "timestamp": (sampled_at - timedelta(seconds=(SANDBOX_HISTORY_COUNT - index - 1) * 30)).timestamp(),
                "ready": ready,
                "creating": creating,
                "target": target,
                "adaptive_target": 5 + (index % 2),
                "deficit": max(0, target - ready - creating),
                "arrival_rate_per_min": round(0.4 + (index % 5) * 0.2, 3),
                "refill_p95_s": round(2.8 + (index % 4) * 0.35, 2),
                "demo_seed_id": MARKER,
            }
        )
    return user_rows, workspace, environment_rows, runtime_rows, execution_rows, samples


def _clear_sandbox_demo_rows(connection: object) -> None:
    execution_filter = SandboxExecutionModel.request_id.like(f"{PREFIX}sandbox-request-%")
    execution_ids = select(SandboxExecutionModel.id).where(execution_filter)
    environment_ids = select(SandboxEnvironmentModel.id).where(
        SandboxEnvironmentModel.resource_profile_id == SANDBOX_ENVIRONMENT_PROFILE
    )
    connection.execute(delete(SandboxArtifactModel).where(SandboxArtifactModel.execution_id.in_(execution_ids)))
    connection.execute(delete(SandboxLeaseModel).where(SandboxLeaseModel.environment_id.in_(environment_ids)))
    connection.execute(delete(SandboxExecutionModel).where(execution_filter))
    connection.execute(
        delete(SandboxRuntimeInstanceModel).where(
            SandboxRuntimeInstanceModel.external_runtime_id.like(f"{PREFIX}runtime-%")
        )
    )
    connection.execute(
        delete(WorkspaceMemberModel).where(
            WorkspaceMemberModel.workspace_id == _uuid("sandbox-workspace")
        )
    )
    connection.execute(
        delete(SandboxEnvironmentModel).where(
            SandboxEnvironmentModel.resource_profile_id == SANDBOX_ENVIRONMENT_PROFILE
        )
    )
    connection.execute(delete(WorkspaceModel).where(WorkspaceModel.slug == SANDBOX_WORKSPACE_SLUG))
    connection.execute(delete(UserModel).where(UserModel.username.like(f"{PREFIX}user-%")))


def _seed_sandbox_capacity_samples(samples: list[dict]) -> str:
    """Best-effort seed of only demo members in the shared Redis series."""
    redis_url = settings.NLP_AGENT_REDIS_URL.strip()
    if not redis_url:
        return "Redis 未配置，容量历史将在监控进程运行后逐步采样"
    if not is_local_endpoint(redis_url):
        return "Redis 不是本机地址，已跳过模拟容量历史写入"
    try:
        import redis

        key = "nova:sandbox:metrics:capacity"
        client = redis.Redis.from_url(redis_url, decode_responses=True)
        client.ping()
        existing = client.zrange(key, 0, -1)
        demo_members: list[str] = []
        for member in existing:
            try:
                if json.loads(member).get("demo_seed_id") == MARKER:
                    demo_members.append(member)
            except (TypeError, json.JSONDecodeError):
                continue
        if demo_members:
            client.zrem(key, *demo_members)
        if samples:
            client.zadd(
                key,
                {
                    json.dumps(sample, separators=(",", ":"), sort_keys=True): float(sample["timestamp"])
                    for sample in samples
                },
            )
            client.expire(key, max(60, int(settings.NLP_AGENT_SANDBOX_METRICS_RETENTION_S)))
        client.close()
        return f"已写入 {len(samples)} 个容量历史采样" if samples else "已清理容量历史中的模拟采样"
    except Exception as error:
        return f"Redis 容量历史未写入（{type(error).__name__}），不影响 MySQL 模拟数据"


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed synthetic monitoring data into local MySQL")
    parser.add_argument("--clear", action="store_true", help="remove only monitor-demo-v1 rows")
    parser.add_argument("--count", type=int, default=96, help="number of synthetic logical requests to create (default: 96)")
    args = parser.parse_args()
    if args.count < 1:
        raise SystemExit("--count must be at least 1")
    database_url = settings.NLP_AGENT_DATABASE_URL.strip()
    if not database_url or not is_local_endpoint(database_url):
        raise SystemExit("Refusing to seed: NLP_AGENT_DATABASE_URL must point to localhost")

    engine = create_engine(database_url.replace("mysql+aiomysql://", "mysql+pymysql://"), pool_pre_ping=True)
    envelopes, usage_rows = _build_observability(count=args.count)
    trace_ids = [
        envelope.payload.trace_id
        for envelope in envelopes
        if envelope.kind == "trace"
    ]
    span_ids = [
        envelope.payload.span_id
        for envelope in envelopes
        if envelope.kind == "span" and envelope.payload.kind == SpanKind.MODEL
    ]
    (
        sandbox_users,
        sandbox_workspace,
        sandbox_environments,
        sandbox_runtimes,
        sandbox_executions,
        sandbox_samples,
    ) = _build_sandbox_demo(count=args.count, trace_ids=trace_ids, span_ids=span_ids)
    with engine.begin() as connection:
        _clear_sandbox_demo_rows(connection)
        connection.execute(delete(ObservabilityRecordModel).where(ObservabilityRecordModel.record_key.like(f"{PREFIX}%")))
        connection.execute(delete(UsageEventModel).where(UsageEventModel.operation_id.like(f"{PREFIX}%")))
        if not args.clear:
            connection.execute(insert(UserModel), sandbox_users)
            connection.execute(insert(WorkspaceModel), sandbox_workspace)
            connection.execute(insert(SandboxEnvironmentModel), sandbox_environments)
            connection.execute(insert(SandboxRuntimeInstanceModel), sandbox_runtimes)
            connection.execute(insert(SandboxExecutionModel), sandbox_executions)
            for envelope in envelopes:
                payload = envelope.model_dump(mode="json")
                item = payload["payload"]
                key = str(
                    item.get("event_id")
                    if envelope.kind == "event"
                    else item.get("span_id")
                    if envelope.kind == "span"
                    else item.get("trace_id")
                )
                connection.execute(insert(ObservabilityRecordModel).values(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"nlp-agent:{envelope.kind}:{key}")),
                    kind=envelope.kind, record_key=key, trace_id=item.get("trace_id"),
                    session_id=item.get("session_id"), turn_id=item.get("turn_id"),
                    status=item.get("status"), payload_json=payload,
                ))
            connection.execute(insert(UsageEventModel), usage_rows)
    engine.dispose()
    sandbox_capacity_status = _seed_sandbox_capacity_samples([] if args.clear else sandbox_samples)
    print("cleared monitor-demo-v1 rows")
    if not args.clear:
        print(
            f"seeded {len(envelopes)} observability records, {len(usage_rows)} usage events, "
            f"{len(sandbox_runtimes)} sandbox runtimes, {len(sandbox_executions)} sandbox executions"
        )
        print(sandbox_capacity_status)
    else:
        print(sandbox_capacity_status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
