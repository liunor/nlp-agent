from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.rbac import Permission, authorization_service
from server.auth.dependencies import Principal, get_db_session
from configs.settings import settings
from server.infrastructure.mysql.models import SandboxExecutionModel, SandboxRuntimeInstanceModel
from server.rbac.service import rbac_service

from .developer import capacity_snapshot, summarize_execution_latency, summarize_runtime_states
from .events import default_sandbox_event_store
from .metrics import default_sandbox_metrics_store
from .commands import create_sandbox_manager_command_store
from .optimization import AdaptivePoolPolicy, load_preload_matrix

router = APIRouter(prefix="/api/v1/developer/sandbox", tags=["developer-sandbox"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]


class PrewarmBody(BaseModel):
    expected_sessions: int = Field(ge=0, le=100_000)
    sessions_per_runtime: int = Field(default=1, ge=1, le=100)
    profile_id: str = Field(default="python-base", min_length=1, max_length=64)
    execute_at: datetime | None = None


def _require_monitor(principal: Principal) -> None:
    authorization_service.require(principal, Permission.SYSTEM_RUNTIME_MONITOR)


def _runtime_payload(row: SandboxRuntimeInstanceModel, *, detail: bool = False) -> dict[str, object | None]:
    payload: dict[str, object | None] = {
        "id": str(row.id),
        "state": row.state,
        "node_id": row.node_id,
        "runtime_kind": row.runtime_kind,
        "resource_profile_id": row.resource_profile_id,
        "external_runtime_id": row.external_runtime_id,
        "failure_reason": row.failure_reason,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if detail:
        payload.update(
            {
                "image_digest": row.image_digest,
                "environment_id": str(row.environment_id) if row.environment_id else None,
                "generation": row.generation,
                "last_heartbeat_at": row.last_heartbeat_at.isoformat() if row.last_heartbeat_at else None,
            }
        )
    return payload


def _execution_payload(row: SandboxExecutionModel) -> dict[str, object | None]:
    return {
        "id": str(row.id),
        "owner_user_id": str(row.owner_user_id),
        "environment_id": str(row.environment_id),
        "runtime_instance_id": str(row.runtime_instance_id) if row.runtime_instance_id else None,
        "status": row.status,
        "generation": row.generation,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "exit_reason": row.exit_reason,
        "trace_id": row.trace_id,
        "span_id": row.span_id,
        "parent_span_id": row.parent_span_id,
    }


@router.get("/overview")
async def sandbox_overview(request: Request, db: DbSession, principal: Principal) -> dict[str, object]:
    _require_monitor(principal)
    rows = (await db.execute(select(SandboxRuntimeInstanceModel.state, func.count()).group_by(SandboxRuntimeInstanceModel.state))).all()
    runtime_states = summarize_runtime_states([(str(state), int(count)) for state, count in rows])
    execution_rows = (await db.execute(
        select(SandboxExecutionModel.started_at, SandboxExecutionModel.completed_at)
        .where(SandboxExecutionModel.started_at.is_not(None), SandboxExecutionModel.completed_at.is_not(None))
        .order_by(SandboxExecutionModel.created_at.desc()).limit(1000)
    )).all()
    active_execution_rows = (await db.execute(
        select(SandboxExecutionModel)
        .where(SandboxExecutionModel.status == "running")
        .order_by(SandboxExecutionModel.created_at.desc())
        .limit(100)
    )).scalars().all()
    failed_execution_rows = (await db.execute(
        select(SandboxExecutionModel)
        .where(SandboxExecutionModel.status == "failed")
        .order_by(SandboxExecutionModel.created_at.desc())
        .limit(50)
    )).scalars().all()
    failed_runtime_rows = (await db.execute(
        select(SandboxRuntimeInstanceModel)
        .where(SandboxRuntimeInstanceModel.state == "failed")
        .order_by(SandboxRuntimeInstanceModel.updated_at.desc())
        .limit(50)
    )).scalars().all()
    durations: list[float] = []
    sampled_now = datetime.now(UTC)
    observed_arrivals = 0
    for started_at, completed_at in execution_rows:
        if started_at is not None:
            started_for_rate = started_at.replace(tzinfo=UTC) if started_at.tzinfo is None else started_at
            if timedelta(0) <= sampled_now - started_for_rate <= timedelta(minutes=5):
                observed_arrivals += 1
        if started_at is None or completed_at is None:
            continue
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=UTC)
        duration = (completed_at - started_at).total_seconds() * 1000
        if duration >= 0:
            durations.append(duration)
    capacity = capacity_snapshot(runtime_states, target=settings.NLP_AGENT_SANDBOX_WARM_POOL_READY_TARGET)
    adaptive_target = AdaptivePoolPolicy(
        ready_min=settings.NLP_AGENT_SANDBOX_WARM_POOL_READY_MIN,
        ready_max=settings.NLP_AGENT_SANDBOX_WARM_POOL_READY_MAX,
        burst_buffer=settings.NLP_AGENT_SANDBOX_BURST_BUFFER,
    ).target_for(
        arrival_rate_per_min=round(observed_arrivals / 5.0, 3),
        refill_p95_s=settings.NLP_AGENT_SANDBOX_REFILL_P95_S,
    )
    capacity["adaptive_target"] = adaptive_target
    manager = getattr(request.app.state, "sandbox_manager", None)
    snapshot = getattr(manager, "capacity_snapshot", None)
    if snapshot is not None:
        try:
            manager_capacity = await snapshot()
            for key in ("ready", "creating", "target", "deficit", "adaptive_target"):
                if key in manager_capacity:
                    capacity[key] = int(manager_capacity[key])
            adaptive_target = int(capacity["adaptive_target"])
        except Exception:
            # The dashboard remains useful during a Manager restart; the
            # database-derived sample above is the safe fallback.
            pass
    alerts: list[dict[str, str]] = []
    if capacity["deficit"] > 0:
        alerts.append({"code": "pool_deficit", "severity": "warning", "message": "Warm Pool ready capacity is below target."})
    if runtime_states["failed"] > 0:
        alerts.append({"code": "runtime_failed", "severity": "critical", "message": "Sandbox runtimes require reconciliation."})
    sample = {
        "timestamp": sampled_now.timestamp(),
        "ready": capacity["ready"], "creating": capacity["creating"],
        "target": capacity["target"], "deficit": capacity["deficit"],
        "adaptive_target": adaptive_target,
        "arrival_rate_per_min": round(observed_arrivals / 5.0, 3),
        "refill_p95_s": settings.NLP_AGENT_SANDBOX_REFILL_P95_S,
    }
    history = [sample]
    if default_sandbox_metrics_store is not None:
        try:
            history = await default_sandbox_metrics_store.record(sample)
        except Exception:
            history = [sample]
    return {
        "runtime_states": runtime_states,
        "capacity": capacity,
        "execution_latency": summarize_execution_latency(durations),
        "active_executions": [_execution_payload(row) for row in active_execution_rows],
        "recent_failures": [
            {"kind": "execution", **_execution_payload(row)} for row in failed_execution_rows
        ] + [
            {
                "kind": "runtime",
                "id": str(row.id),
                "state": row.state,
                "failure_reason": row.failure_reason,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in failed_runtime_rows
        ],
        "alerts": alerts,
        "capacity_history": history,
        "sampled_at": datetime.now(UTC).isoformat(),
    }


@router.get("/runtimes")
async def list_sandbox_runtimes(db: DbSession, principal: Principal) -> dict[str, list[dict[str, object | None]]]:
    _require_monitor(principal)
    rows = (await db.execute(
        select(SandboxRuntimeInstanceModel).order_by(SandboxRuntimeInstanceModel.updated_at.desc()).limit(200)
    )).scalars().all()
    return {"items": [_runtime_payload(row) for row in rows]}


@router.get("/preload-compatibility")
async def preload_compatibility(principal: Principal) -> dict[str, object]:
    _require_monitor(principal)
    configured = settings.NLP_AGENT_SANDBOX_PRELOAD_MATRIX_PATH.strip()
    path = Path(configured) if configured else settings.BASE_DIR / "configs" / "sandbox_preload_matrix.json"
    return load_preload_matrix(path)


@router.post("/capacity/prewarm")
async def request_capacity_prewarm(body: PrewarmBody, principal: Principal) -> dict[str, object]:
    """Queue a control-plane command; Web never opens Docker itself."""
    _require_monitor(principal)
    if body.profile_id != "python-base":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only the python-base Sandbox Manager profile is currently schedulable.",
        )
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
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sandbox Manager command stream is unavailable.",
        )
    try:
        command_id = await store.request_pool_target(
            profile_id=body.profile_id,
            target=target,
            reason=f"developer.prewarm:{principal.user_id}",
            execute_at=body.execute_at.isoformat() if body.execute_at else None,
        )
    finally:
        await store.close()
    return {
        "command_id": command_id,
        "profile_id": body.profile_id,
        "target": target,
        "expected_sessions": body.expected_sessions,
        "execute_at": body.execute_at.isoformat() if body.execute_at else None,
    }


@router.post("/runtimes/{runtime_id}/drain")
async def drain_sandbox_runtime(runtime_id: str, db: DbSession, principal: Principal) -> dict[str, str]:
    _require_monitor(principal)
    runtime = await db.get(SandboxRuntimeInstanceModel, runtime_id, with_for_update=True)
    if runtime is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sandbox runtime not found.")
    if runtime.state not in {"assigned", "ready_unbound", "claiming"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Runtime is already {runtime.state}.")
    runtime.state = "draining"
    runtime.failure_reason = "developer.drain_requested"
    await rbac_service.audit(
        db, actor_user_id=principal.user_id, target_user_id=None, decision="allow",
        reason_code="sandbox_runtime_drain_requested", permission_code=Permission.SYSTEM_RUNTIME_MONITOR.value,
        resource_type="sandbox_runtime", resource_id=runtime_id,
    )
    await db.commit()
    return {"id": runtime_id, "state": runtime.state}


@router.get("/runtimes/{runtime_id}")
async def get_sandbox_runtime(runtime_id: str, db: DbSession, principal: Principal) -> dict[str, object | None]:
    _require_monitor(principal)
    runtime = await db.get(SandboxRuntimeInstanceModel, runtime_id)
    if runtime is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sandbox runtime not found.")
    return _runtime_payload(runtime, detail=True)


@router.get("/executions")
async def list_sandbox_executions(
    db: DbSession,
    principal: Principal,
    status_filter: str | None = None,
) -> dict[str, list[dict[str, object | None]]]:
    _require_monitor(principal)
    query = select(SandboxExecutionModel).order_by(SandboxExecutionModel.created_at.desc()).limit(200)
    if status_filter:
        query = query.where(SandboxExecutionModel.status == status_filter)
    rows = (await db.execute(query)).scalars().all()
    return {"items": [_execution_payload(row) for row in rows]}


@router.get("/executions/{execution_id}/events")
async def replay_sandbox_execution_events(
    execution_id: str,
    db: DbSession,
    principal: Principal,
    after_event_id: str | None = None,
) -> dict[str, object]:
    _require_monitor(principal)
    execution = await db.get(SandboxExecutionModel, execution_id)
    if execution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sandbox execution not found.")
    events = await default_sandbox_event_store.replay(
        execution_id, user_id=str(execution.owner_user_id), after_event_id=after_event_id,
    )
    return {"execution_id": execution_id, "events": events}
