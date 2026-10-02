"""Shared, bounded data queries for the isolated sandbox monitor plane."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from configs.settings import settings
from server.infrastructure.mysql.models import SandboxExecutionModel, SandboxLeaseModel, SandboxRuntimeInstanceModel
from server.rbac.service import rbac_service

from .commands import create_sandbox_manager_command_store
from .developer import capacity_alerts, capacity_snapshot, summarize_execution_latency, summarize_runtime_states
from .events import default_sandbox_event_store
from .metrics import (
    aggregate_sandbox_capacity_samples,
    collect_sandbox_lease_demand,
    default_sandbox_metrics_store,
    sandbox_arrival_rate_per_min,
)
from .optimization import AdaptivePoolPolicy, load_preload_matrix


_MANAGER_CAPACITY_TIMEOUT_SECONDS = 3.0


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def runtime_payload(row: SandboxRuntimeInstanceModel, *, detail: bool = False) -> dict[str, object | None]:
    payload: dict[str, object | None] = {
        "id": str(row.id),
        "state": row.state,
        "node_id": row.node_id,
        "runtime_kind": row.runtime_kind,
        "resource_profile_id": row.resource_profile_id,
        "external_runtime_id": row.external_runtime_id,
        "failure_reason": row.failure_reason,
        "updated_at": _iso(row.updated_at),
    }
    if detail:
        payload.update(
            {
                "image_digest": row.image_digest,
                "environment_id": str(row.environment_id) if row.environment_id else None,
                "generation": row.generation,
                "last_heartbeat_at": _iso(row.last_heartbeat_at),
            }
        )
    return payload


def execution_payload(row: SandboxExecutionModel) -> dict[str, object | None]:
    return {
        "id": str(row.id),
        "owner_user_id": str(row.owner_user_id),
        "environment_id": str(row.environment_id),
        "runtime_instance_id": str(row.runtime_instance_id) if row.runtime_instance_id else None,
        "status": row.status,
        "generation": row.generation,
        "started_at": _iso(row.started_at),
        "completed_at": _iso(row.completed_at),
        "exit_reason": row.exit_reason,
        "trace_id": row.trace_id,
        "span_id": row.span_id,
        "parent_span_id": row.parent_span_id,
    }


def execution_log_payload(row: Any) -> dict[str, object | None]:
    status = str(getattr(row, "status", "unknown"))
    event_type = {
        "running": "execution.started",
        "completed": "execution.completed",
        "failed": "execution.failed",
        "error": "execution.failed",
        "timeout": "execution.failed",
    }.get(status, f"execution.{status}")
    level = "error" if status in {"failed", "error", "timeout"} else "info"
    reason = str(getattr(row, "exit_reason", "") or "").strip()
    message = reason if level == "error" and reason else {
        "running": "沙箱执行正在运行",
        "completed": "沙箱执行完成",
    }.get(status, f"沙箱执行状态：{status}")
    timestamp = (
        getattr(row, "completed_at", None)
        or getattr(row, "started_at", None)
        or getattr(row, "created_at", None)
    )
    execution_id = str(getattr(row, "id", ""))
    runtime_id = getattr(row, "runtime_instance_id", None)
    return {
        "id": f"execution:{execution_id}:{status}",
        "timestamp": _iso(timestamp),
        "level": level,
        "event_type": event_type,
        "execution_id": execution_id,
        "runtime_id": str(runtime_id) if runtime_id else None,
        "message": message,
    }


def runtime_log_payload(row: Any) -> dict[str, object | None]:
    state = str(getattr(row, "state", "unknown"))
    timestamp = getattr(row, "updated_at", None) or getattr(row, "created_at", None)
    reason = str(getattr(row, "failure_reason", "") or "").strip()
    return {
        "id": f"runtime:{row.id}:{state}:{_iso(timestamp) or 'unknown'}",
        "timestamp": _iso(timestamp),
        "level": "error" if state == "failed" else "warning",
        "event_type": f"runtime.{state}",
        "runtime_id": str(row.id),
        "message": reason or f"运行时状态：{state}",
    }


def _runtime_state_counts_query():
    return select(
        SandboxRuntimeInstanceModel.state,
        func.count(),
    ).group_by(SandboxRuntimeInstanceModel.state)


async def sandbox_overview(
    db: AsyncSession,
    request: Any,
    *,
    history_window_minutes: int = 30,
) -> dict[str, object]:
    history_window_minutes = max(10, min(24 * 60, int(history_window_minutes)))
    sampled_now = datetime.now(UTC)
    recent_failure_cutoff = sampled_now.replace(tzinfo=None) - timedelta(minutes=15)
    rows = (
        await db.execute(
            _runtime_state_counts_query()
        )
    ).all()
    runtime_states = summarize_runtime_states([(str(state), int(count)) for state, count in rows])
    execution_rows = (
        await db.execute(
            select(SandboxExecutionModel.started_at, SandboxExecutionModel.completed_at)
            .where(
                SandboxExecutionModel.started_at.is_not(None),
                SandboxExecutionModel.completed_at.is_not(None),
            )
            .order_by(SandboxExecutionModel.created_at.desc())
            .limit(1000)
        )
    ).all()
    active_execution_rows = (
        await db.execute(
            select(SandboxExecutionModel)
            .where(SandboxExecutionModel.status == "running")
            .order_by(SandboxExecutionModel.created_at.desc())
            .limit(100)
        )
    ).scalars().all()
    failed_execution_rows = (
        await db.execute(
            select(SandboxExecutionModel)
            .where(
                SandboxExecutionModel.status.in_(("failed", "error", "timeout")),
                func.coalesce(
                    SandboxExecutionModel.completed_at,
                    SandboxExecutionModel.started_at,
                    SandboxExecutionModel.created_at,
                ) >= recent_failure_cutoff,
            )
            .order_by(SandboxExecutionModel.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    failed_runtime_rows = (
        await db.execute(
            select(SandboxRuntimeInstanceModel)
            .where(
                SandboxRuntimeInstanceModel.state == "failed",
                func.coalesce(
                    SandboxRuntimeInstanceModel.updated_at,
                    SandboxRuntimeInstanceModel.created_at,
                ) >= recent_failure_cutoff,
            )
            .order_by(SandboxRuntimeInstanceModel.updated_at.desc())
            .limit(50)
        )
    ).scalars().all()

    durations: list[float] = []
    new_lease_count = int(
        await db.scalar(
            select(func.count()).select_from(SandboxLeaseModel).where(
                SandboxLeaseModel.created_at >= sampled_now.replace(tzinfo=None) - timedelta(minutes=5)
            )
        )
        or 0
    )
    lease_demand = await collect_sandbox_lease_demand(db, now=sampled_now)
    observed_arrival_rate = sandbox_arrival_rate_per_min(
        new_lease_count=new_lease_count,
        window_seconds=300,
    )
    for started_at, completed_at in execution_rows:
        if started_at is None or completed_at is None:
            continue
        start = started_at.replace(tzinfo=UTC) if started_at.tzinfo is None else started_at
        completed = completed_at.replace(tzinfo=UTC) if completed_at.tzinfo is None else completed_at
        duration = (completed - start).total_seconds() * 1000
        if duration >= 0:
            durations.append(duration)

    capacity = capacity_snapshot(
        runtime_states, target=settings.NLP_AGENT_SANDBOX_WARM_POOL_READY_TARGET
    )
    adaptive_target = AdaptivePoolPolicy(
        ready_min=settings.NLP_AGENT_SANDBOX_WARM_POOL_READY_MIN,
        ready_max=settings.NLP_AGENT_SANDBOX_WARM_POOL_READY_MAX,
        burst_buffer=settings.NLP_AGENT_SANDBOX_BURST_BUFFER,
    ).target_for(
        arrival_rate_per_min=observed_arrival_rate,
        refill_p95_s=settings.NLP_AGENT_SANDBOX_REFILL_P95_S,
        unassigned_lease_count=lease_demand.unassigned_count,
    )
    capacity["adaptive_target"] = adaptive_target
    capacity.update(
        {
            "online_count": lease_demand.online_count,
            "active_session_count": lease_demand.active_session_count,
            "unassigned_count": lease_demand.unassigned_count,
            "role_demand": lease_demand.role_demand,
            "target_source": "adaptive",
        }
    )
    manager = getattr(request.app.state, "sandbox_manager", None)
    snapshot = getattr(manager, "capacity_snapshot", None)
    if snapshot is not None:
        try:
            # Capacity is an operational enhancement, not a reason to stall the
            # whole monitor page while Manager is restarting or Redis is down.
            manager_capacity = await asyncio.wait_for(
                snapshot(),
                timeout=_MANAGER_CAPACITY_TIMEOUT_SECONDS,
            )
            for key in (
                "ready", "creating", "target", "deficit", "adaptive_target",
                "assigned", "total", "total_max", "execution_limit",
                "online_count", "active_session_count", "unassigned_count",
                "host_total", "host_total_max", "host_available", "host_budget_blocked_count",
            ):
                if key in manager_capacity and manager_capacity[key] is not None:
                    capacity[key] = int(manager_capacity[key])
            if manager_capacity.get("target_source"):
                capacity["target_source"] = str(manager_capacity["target_source"])
            if manager_capacity.get("manual_target_expires_at") is not None:
                capacity["manual_target_expires_at"] = manager_capacity["manual_target_expires_at"]
            if isinstance(manager_capacity.get("role_demand"), dict):
                capacity["role_demand"] = dict(manager_capacity["role_demand"])
            adaptive_target = int(capacity["adaptive_target"])
        except Exception:
            # The monitor remains useful during a Manager restart; the database
            # sample above is the safe fallback.
            pass

    alerts = capacity_alerts(
        deficit=capacity["deficit"],
        failed_runtime_count=runtime_states["failed"],
        unassigned_count=lease_demand.unassigned_count,
    )

    sample = {
        "timestamp": sampled_now.timestamp(),
        "ready": capacity["ready"],
        "creating": capacity["creating"],
        "assigned": capacity.get("assigned", runtime_states.get("assigned", 0)),
        "total": capacity.get("total", sum(runtime_states.values())),
        "total_max": capacity.get("total_max", settings.NLP_AGENT_SANDBOX_RUNTIME_TOTAL_MAX),
        "online_count": capacity.get("online_count", lease_demand.online_count),
        "active_session_count": capacity.get("active_session_count", lease_demand.active_session_count),
        "unassigned_count": capacity.get("unassigned_count", lease_demand.unassigned_count),
        "role_demand": capacity.get("role_demand", lease_demand.role_demand),
        "target": capacity["target"],
        "deficit": capacity["deficit"],
        "adaptive_target": adaptive_target,
        "target_source": capacity.get("target_source", "adaptive"),
        "manual_target_expires_at": capacity.get("manual_target_expires_at"),
        "arrival_rate_per_min": observed_arrival_rate,
        "new_lease_count": new_lease_count,
        "refill_p95_s": settings.NLP_AGENT_SANDBOX_REFILL_P95_S,
        "host_total": capacity.get("host_total"),
        "host_total_max": capacity.get("host_total_max"),
        "host_available": capacity.get("host_available"),
        "host_budget_blocked_count": capacity.get("host_budget_blocked_count", 0),
    }
    if default_sandbox_metrics_store is not None:
        try:
            sample_interval = max(1, int(settings.NLP_AGENT_SANDBOX_METRICS_SAMPLE_INTERVAL_S))
            history_limit = min(
                2_000,
                max(60, history_window_minutes * 60 // sample_interval + 60),
            )
            history = await default_sandbox_metrics_store.recent(limit=history_limit)
        except Exception:
            history = []
    else:
        history = []
    history.append(sample)
    history = aggregate_sandbox_capacity_samples(
        history,
        now=sampled_now.timestamp(),
        window_seconds=history_window_minutes * 60,
        bucket_seconds=max(30, (history_window_minutes * 60 + 59) // 60),
        max_points=60,
    )

    return {
        "runtime_states": runtime_states,
        "capacity": capacity,
        "execution_latency": summarize_execution_latency(durations),
        "active_executions": len(active_execution_rows),
        "recent_failures": len(failed_execution_rows) + len(failed_runtime_rows),
        "alerts": alerts,
        "capacity_history": history,
        "capacity_history_window_minutes": history_window_minutes,
        "sampled_at": sampled_now.isoformat(),
    }


async def sandbox_logs(
    db: AsyncSession, *, limit: int = 80, since_seconds: int = 600
) -> dict[str, object]:
    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - timedelta(seconds=max(60, since_seconds))
    execution_log_time = func.coalesce(
        SandboxExecutionModel.completed_at,
        SandboxExecutionModel.started_at,
        SandboxExecutionModel.created_at,
    )
    runtime_log_time = func.coalesce(
        SandboxRuntimeInstanceModel.updated_at,
        SandboxRuntimeInstanceModel.created_at,
    )
    executions = (
        await db.execute(
            select(SandboxExecutionModel)
            .where(execution_log_time >= cutoff)
            .order_by(execution_log_time.desc())
            .limit(min(200, limit * 2))
        )
    ).scalars().all()
    runtimes = (
        await db.execute(
            select(SandboxRuntimeInstanceModel)
            .where(
                SandboxRuntimeInstanceModel.state.in_(("failed", "draining")),
                runtime_log_time >= cutoff,
            )
            .order_by(runtime_log_time.desc())
            .limit(min(100, limit))
        )
    ).scalars().all()
    items = [execution_log_payload(row) for row in executions]
    items.extend(runtime_log_payload(row) for row in runtimes)
    items.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    return {
        "items": items[:limit],
        "retention_seconds": max(60, since_seconds),
        "sampled_at": datetime.now(UTC).isoformat(),
    }


async def list_runtimes(
    db: AsyncSession, *, limit: int = 50, offset: int = 0
) -> dict[str, object]:
    page_limit = min(max(1, limit), 100)
    page_offset = max(0, offset)
    total = int((await db.execute(
        select(func.count()).select_from(SandboxRuntimeInstanceModel)
    )).scalar_one() or 0)
    rows = (
        await db.execute(
            select(SandboxRuntimeInstanceModel)
            .order_by(SandboxRuntimeInstanceModel.updated_at.desc())
            .offset(page_offset)
            .limit(page_limit)
        )
    ).scalars().all()
    items = [runtime_payload(row) for row in rows]
    return {
        "items": items,
        "total": total,
        "offset": page_offset,
        "limit": page_limit,
        "has_more": page_offset + len(items) < total,
    }


async def get_runtime(db: AsyncSession, runtime_id: str) -> dict[str, object | None] | None:
    row = await db.get(SandboxRuntimeInstanceModel, runtime_id)
    return runtime_payload(row, detail=True) if row is not None else None


async def list_executions(
    db: AsyncSession,
    *,
    status_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    page_limit = min(max(1, limit), 100)
    page_offset = max(0, offset)
    count_query = select(func.count()).select_from(SandboxExecutionModel)
    query = select(SandboxExecutionModel).order_by(SandboxExecutionModel.created_at.desc())
    if status_filter:
        count_query = count_query.where(SandboxExecutionModel.status == status_filter)
        query = query.where(SandboxExecutionModel.status == status_filter)
    total = int((await db.execute(count_query)).scalar_one() or 0)
    query = query.offset(page_offset).limit(page_limit)
    rows = (await db.execute(query)).scalars().all()
    items = [execution_payload(row) for row in rows]
    return {
        "items": items,
        "total": total,
        "offset": page_offset,
        "limit": page_limit,
        "has_more": page_offset + len(items) < total,
    }


async def drain_runtime(db: AsyncSession, runtime_id: str, principal: Any) -> dict[str, str]:
    runtime = await db.get(SandboxRuntimeInstanceModel, runtime_id, with_for_update=True)
    if runtime is None:
        raise LookupError("Sandbox runtime not found.")
    if runtime.state not in {"assigned", "ready_unbound", "claiming"}:
        raise ValueError(f"Runtime is already {runtime.state}.")
    runtime.state = "draining"
    runtime.failure_reason = "monitor.drain_requested"
    await rbac_service.audit(
        db,
        actor_user_id=principal.user_id,
        target_user_id=None,
        decision="allow",
        reason_code="sandbox_runtime_drain_requested",
        permission_code="system:runtime:reset",
        resource_type="sandbox_runtime",
        resource_id=runtime_id,
    )
    await db.commit()
    return {"id": runtime_id, "state": runtime.state}


async def preload_compatibility() -> dict[str, object]:
    configured = settings.NLP_AGENT_SANDBOX_PRELOAD_MATRIX_PATH.strip()
    path = Path(configured) if configured else settings.BASE_DIR / "configs" / "sandbox_preload_matrix.json"
    return load_preload_matrix(path)


async def request_capacity_prewarm(body: Any, principal: Any) -> dict[str, object]:
    if body.profile_id != "python-base":
        raise ValueError("Only the python-base Sandbox Manager profile is currently schedulable.")
    policy = AdaptivePoolPolicy(
        ready_min=settings.NLP_AGENT_SANDBOX_WARM_POOL_READY_MIN,
        ready_max=settings.NLP_AGENT_SANDBOX_WARM_POOL_READY_MAX,
        burst_buffer=settings.NLP_AGENT_SANDBOX_BURST_BUFFER,
    )
    target = policy.target_before_class(
        expected_sessions=body.expected_sessions,
        sessions_per_runtime=body.sessions_per_runtime,
    )
    store = create_sandbox_manager_command_store()
    if store is None:
        raise RuntimeError("Sandbox Manager command stream is unavailable.")
    try:
        command_id = await store.request_pool_target(
            profile_id=body.profile_id,
            target=target,
            reason=f"monitor.prewarm:{principal.user_id}",
            execute_at=body.execute_at.isoformat() if body.execute_at else None,
            target_ttl_seconds=body.ttl_seconds,
        )
    finally:
        await store.close()
    return {
        "command_id": command_id,
        "profile_id": body.profile_id,
        "target": target,
        "expected_sessions": body.expected_sessions,
        "execute_at": body.execute_at.isoformat() if body.execute_at else None,
        "ttl_seconds": body.ttl_seconds,
    }


async def replay_execution_events(
    db: AsyncSession, execution_id: str, *, after_event_id: str | None = None
) -> dict[str, object]:
    execution = await db.get(SandboxExecutionModel, execution_id)
    if execution is None:
        raise LookupError("Sandbox execution not found.")
    events = await default_sandbox_event_store.replay(
        execution_id,
        user_id=str(execution.owner_user_id),
        after_event_id=after_event_id,
    )
    return {"execution_id": execution_id, "events": events}
