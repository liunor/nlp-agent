"""Authenticated Phase 0 sandbox lifecycle endpoints."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import (
    Principal,
    WriteClaims,
    get_database_session_claims,
    get_db_session,
)
from server.web.database_auth import DatabaseSessionClaims
from server.infrastructure.mysql.models import SandboxEnvironmentModel, SandboxExecutionModel, SandboxLeaseModel
from configs.settings import settings
from server.sandbox.gateway import SandboxGateway
from server.sandbox.confirmation import SandboxConfirmationSigner
from server.sandbox.ticket import SandboxTicketSigner
from server.sandbox.events import SandboxEventStore, default_sandbox_event_store
from server.sandbox.execution_events import (
    execution_failure_payload,
    execution_output_streams,
    execution_result_failure_payload,
    execution_result_failed,
)
from server.web.protocol import control_event

from .contracts import SandboxScope
from .service import sandbox_lifecycle_service
from .inmemory_runtime import InMemoryRuntime
from .artifact_persistence import persist_runtime_artifacts


router = APIRouter(prefix="/api/v1/sandbox", tags=["sandbox"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
DatabaseClaims = Annotated[DatabaseSessionClaims, Depends(get_database_session_claims)]
inmemory_runtime = InMemoryRuntime()
sandbox_events = default_sandbox_event_store


class ExecuteBody(BaseModel):
    source: str = Field(min_length=1, max_length=20_000)
    ticket: str | None = Field(default=None, max_length=2_048)


class RuntimeTicketBody(BaseModel):
    ticket: str | None = Field(default=None, max_length=2_048)


class ConfirmationBody(BaseModel):
    tool_name: str = Field(pattern=r"^sandbox_(run_active_kernel|reset)$")
    source: str = Field(default="", max_length=20_000)


def _sandbox_gateway(request: Request) -> SandboxGateway:
    existing = getattr(request.app.state, "sandbox_execution_gateway", None)
    if existing is not None:
        return existing
    factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
    if factory is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sandbox database is unavailable.")
    mode = settings.NLP_AGENT_SANDBOX_RUNTIME_MODE.strip().lower()
    secret = settings.NLP_AGENT_WEB_SECRET.strip()
    if not secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sandbox ticket signing is not configured.")
    manager = None
    if mode == "docker":
        # The Web process may receive an explicitly injected Manager client,
        # but it must never construct a Docker adapter or open the Engine
        # socket.  The isolated manager runner owns that capability.
        manager = getattr(request.app.state, "sandbox_manager", None)
        if manager is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Isolated Sandbox Manager is not connected.",
            )
    if mode not in {"inmemory", "docker"}:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sandbox runtime is not enabled.")
    if mode == "docker" and isinstance(sandbox_events, SandboxEventStore):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis Stream event storage is required for Docker sandbox mode.")
    gateway = SandboxGateway(
        mode=mode, session_factory=factory, ticket_signer=SandboxTicketSigner(secret),
        manager=manager, inmemory=inmemory_runtime,
    )
    request.app.state.sandbox_execution_gateway = gateway
    return gateway


@router.get("")
async def describe_sandbox(
    db: DbSession,
    principal: Principal,
    claims: DatabaseClaims,
) -> dict:
    return await sandbox_lifecycle_service.describe(
        db, SandboxScope.from_authenticated_request(principal, claims)
    )


@router.post("/confirmations")
async def issue_sandbox_confirmation(
    body: ConfirmationBody,
    principal: Principal,
    claims: DatabaseClaims,
    _write_claims: WriteClaims,
) -> dict[str, object]:
    """Issue a user-approved, operation- and code-bound high-risk credential."""
    if principal.user_id != claims.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sandbox identity mismatch.")
    if body.tool_name == "sandbox_run_active_kernel" and not body.source.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Source is required for active-kernel confirmation.")
    secret = settings.NLP_AGENT_WEB_SECRET.strip()
    if not secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sandbox confirmation signing is not configured.")
    code_hash = hashlib.sha256(body.source.encode("utf-8")).hexdigest() if body.source else ""
    token = SandboxConfirmationSigner(secret).issue(
        user_id=claims.user_id,
        session_id=claims.session_id,
        tool_name=body.tool_name,
        code_hash=code_hash,
    )
    return {"confirmation_token": token, "tool_name": body.tool_name, "code_hash": code_hash, "expires_in": 120}


@router.post("/lease")
async def ensure_sandbox_lease(
    request: Request,
    db: DbSession,
    principal: Principal,
    claims: DatabaseClaims,
    _write_claims: WriteClaims,
) -> dict:
    return await _sandbox_gateway(request).open(db, SandboxScope.from_authenticated_request(principal, claims))


@router.post("/execute")
async def execute_sandbox(
    request: Request,
    body: ExecuteBody,
    db: DbSession,
    principal: Principal,
    claims: DatabaseClaims,
    _write_claims: WriteClaims,
) -> dict:
    scope = SandboxScope.from_authenticated_request(principal, claims)
    execution_id = str(uuid4())
    environment = await db.scalar(select(SandboxEnvironmentModel).where(SandboxEnvironmentModel.owner_user_id == scope.owner_user_id))
    lease = None
    if environment is not None:
        lease = await db.scalar(select(SandboxLeaseModel).where(
            SandboxLeaseModel.environment_id == environment.id,
            SandboxLeaseModel.auth_session_id == scope.auth_session_id,
            SandboxLeaseModel.state == "active",
        ))
    if environment is None or lease is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Sandbox lease is not active.")
    execution = SandboxExecutionModel(
        id=execution_id,
        environment_id=environment.id,
        runtime_instance_id=lease.runtime_instance_id,
        lease_id=lease.id,
        owner_user_id=scope.owner_user_id,
        workspace_id=scope.workspace_id,
        actor_type="browser",
        request_id=execution_id,
        code_hash=hashlib.sha256(body.source.encode("utf-8")).hexdigest(),
        status="running",
        generation=scope.generation,
        started_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(execution)
    await db.flush()
    await sandbox_events.append(execution_id, user_id=scope.owner_user_id, event_type="execution.started", payload={})
    await request.app.state.hub.broadcast(control_event("sandbox.execution.started", payload={"execution_id": execution_id, "seq": 1}), user_id=scope.owner_user_id)
    try:
        result = await _sandbox_gateway(request).execute(scope, source=body.source, ticket=body.ticket)
        persisted_artifacts = []
        store_root = settings.NLP_AGENT_SANDBOX_ARTIFACT_STORE_ROOT.strip()
        if store_root:
            persisted_artifacts = persist_runtime_artifacts(
                db=db, execution_id=execution_id, owner_user_id=scope.owner_user_id,
                payload=result.get("artifacts"), store_root=Path(store_root),
                ttl_seconds=settings.NLP_AGENT_SANDBOX_ARTIFACT_TTL_S,
            )
        for stream, output in execution_output_streams(result):
            event = await sandbox_events.append(
                execution_id,
                user_id=scope.owner_user_id,
                event_type="execution.output",
                payload={"stream": stream, "text": output},
            )
            await request.app.state.hub.broadcast(
                control_event(
                    "sandbox.execution.output",
                    payload={"execution_id": execution_id, **event},
                ),
                user_id=scope.owner_user_id,
            )
        execution.status = str(result.get("status") or "completed")
        execution.completed_at = datetime.now(UTC).replace(tzinfo=None)
        if execution_result_failed(result):
            event = await sandbox_events.append(
                execution_id,
                user_id=scope.owner_user_id,
                event_type="execution.failed",
                payload=execution_result_failure_payload(result),
            )
            await request.app.state.hub.broadcast(
                control_event(
                    "sandbox.execution.failed",
                    payload={"execution_id": execution_id, **event},
                ),
                user_id=scope.owner_user_id,
            )
        else:
            event = await sandbox_events.append(
                execution_id,
                user_id=scope.owner_user_id,
                event_type="execution.completed",
                payload={},
            )
            await request.app.state.hub.broadcast(
                control_event(
                    "sandbox.execution.completed",
                    payload={"execution_id": execution_id, **event},
                ),
                user_id=scope.owner_user_id,
            )
        await db.commit()
        return {**result, "execution_id": execution_id, "artifacts": [{"id": artifact.id, "name": artifact.locator.rsplit("/", 1)[-1], "mime_type": artifact.mime_type} for artifact in persisted_artifacts]}
    except PermissionError as error:
        execution.status = "failed"
        execution.exit_reason = str(error)[:128]
        execution.completed_at = datetime.now(UTC).replace(tzinfo=None)
        event = await sandbox_events.append(
            execution_id,
            user_id=scope.owner_user_id,
            event_type="execution.failed",
            payload=execution_failure_payload(error),
        )
        await request.app.state.hub.broadcast(
            control_event(
                "sandbox.execution.failed",
                payload={"execution_id": execution_id, **event},
            ),
            user_id=scope.owner_user_id,
        )
        await db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except Exception as error:
        execution.status = "failed"
        execution.exit_reason = f"{type(error).__name__}: {error}"[:128]
        execution.completed_at = datetime.now(UTC).replace(tzinfo=None)
        event = await sandbox_events.append(
            execution_id,
            user_id=scope.owner_user_id,
            event_type="execution.failed",
            payload=execution_failure_payload(error),
        )
        await request.app.state.hub.broadcast(
            control_event(
                "sandbox.execution.failed",
                payload={"execution_id": execution_id, **event},
            ),
            user_id=scope.owner_user_id,
        )
        await db.commit()
        raise


@router.get("/executions/{execution_id}/events")
async def replay_execution_events(
    execution_id: str,
    db: DbSession,
    principal: Principal,
    claims: DatabaseClaims,
    after_event_id: str | None = None,
) -> dict:
    scope = SandboxScope.from_authenticated_request(principal, claims)
    execution = await db.get(SandboxExecutionModel, execution_id)
    if (
        execution is None
        or str(execution.owner_user_id) != scope.owner_user_id
        or str(execution.workspace_id) != scope.workspace_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return {"execution_id": execution_id, "events": await sandbox_events.replay(execution_id, user_id=scope.owner_user_id, after_event_id=after_event_id)}


@router.post("/restart")
async def restart_sandbox(
    request: Request,
    body: RuntimeTicketBody,
    principal: Principal,
    claims: DatabaseClaims,
    _write_claims: WriteClaims,
) -> dict:
    scope = SandboxScope.from_authenticated_request(principal, claims)
    try:
        return await _sandbox_gateway(request).restart(scope, ticket=body.ticket)
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
