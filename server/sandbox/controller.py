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
    append_event_with_retry,
    execution_failure_payload,
    execution_output_streams,
    execution_result_failure_payload,
    execution_result_failed,
    SandboxEventDeliveryError,
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


def _sandbox_session_factory(request: Request):
    """Return the database factory used for short, independently committed writes.

    The request dependency deliberately owns a transaction for the lifetime of
    the HTTP request.  A Docker Manager uses another database connection, so
    execution records must be committed through this factory before any RPC is
    sent to the Manager.
    """
    factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sandbox database is unavailable.",
        )
    return factory


async def _emit_execution_event(
    request: Request,
    *,
    execution_id: str,
    user_id: str,
    event_type: str,
    payload: dict[str, object],
) -> dict[str, object]:
    event = await append_event_with_retry(
        lambda: sandbox_events.append(
            execution_id,
            user_id=user_id,
            event_type=event_type,
            payload=payload,
        )
    )
    # Redis append is the durable delivery boundary.  A websocket broadcast
    # can fail for a disconnected browser without invalidating the persisted
    # event; the replay endpoint will recover it on the next request.
    try:
        await request.app.state.hub.broadcast(
            control_event(
                f"sandbox.{event_type}",
                payload={"execution_id": execution_id, **event},
            ),
            user_id=user_id,
        )
    except Exception:
        pass
    return event


async def _finish_execution(
    request: Request,
    session_factory,
    *,
    execution_id: str,
    owner_user_id: str,
    status_value: str,
    result: dict[str, object] | None = None,
    error: BaseException | None = None,
) -> list[object]:
    """Persist the terminal execution state in a fresh short transaction."""
    persisted_artifacts: list[object] = []
    event_delivery_error: BaseException | None = None
    async with session_factory.begin() as session:
        execution = await session.get(
            SandboxExecutionModel,
            execution_id,
            with_for_update=True,
        )
        if execution is None:
            raise RuntimeError("sandbox execution record disappeared before completion")
        if result is not None:
            store_root = settings.NLP_AGENT_SANDBOX_ARTIFACT_STORE_ROOT.strip()
            if store_root:
                persisted_artifacts = persist_runtime_artifacts(
                    db=session,
                    execution_id=execution_id,
                    owner_user_id=owner_user_id,
                    payload=result.get("artifacts"),
                    store_root=Path(store_root),
                    ttl_seconds=settings.NLP_AGENT_SANDBOX_ARTIFACT_TTL_S,
                )
        execution.status = status_value
        execution.completed_at = datetime.now(UTC).replace(tzinfo=None)
        if error is not None:
            execution.exit_reason = f"{type(error).__name__}: {error}"[:128]
        elif result is not None and execution_result_failed(result):
            execution.exit_reason = execution_result_failure_payload(result)["error"]
        failed = error is not None or execution_result_failed(result or {})
        event_type = "execution.failed" if failed else "execution.completed"
        payload = (
            execution_failure_payload(error)
            if error is not None
            else execution_result_failure_payload(result or {})
            if failed
            else {}
        )
        try:
            if result is not None:
                for stream, output in execution_output_streams(result):
                    await _emit_execution_event(
                        request,
                        execution_id=execution_id,
                        user_id=owner_user_id,
                        event_type="execution.output",
                        payload={"stream": stream, "text": output},
                    )
            await _emit_execution_event(
                request,
                execution_id=execution_id,
                user_id=owner_user_id,
                event_type=event_type,
                payload=payload,
            )
        except Exception as delivery_error:
            # Redis is outside the SQL transaction, so compensate explicitly:
            # never commit a successful terminal state when its terminal event
            # was not delivered.  The failure event is best effort and the DB
            # row remains authoritative if Redis is completely unavailable.
            event_delivery_error = delivery_error
            execution.status = "failed"
            execution.completed_at = datetime.now(UTC).replace(tzinfo=None)
            execution.exit_reason = f"event delivery failed: {delivery_error}"[:128]
            summary = dict(execution.resource_summary_json or {})
            summary["event_delivery"] = {
                "status": "failed",
                "error": str(delivery_error)[:128],
            }
            execution.resource_summary_json = summary
            try:
                await _emit_execution_event(
                    request,
                    execution_id=execution_id,
                    user_id=owner_user_id,
                    event_type="execution.failed",
                    payload=execution_failure_payload(delivery_error),
                )
            except Exception:
                pass
    if event_delivery_error is not None:
        raise SandboxEventDeliveryError(event_delivery_error)
    return persisted_artifacts


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
    del db  # The request-scoped transaction must not span the Manager RPC.
    scope = SandboxScope.from_authenticated_request(principal, claims)
    execution_id = str(uuid4())
    session_factory = _sandbox_session_factory(request)

    # Keep the execution envelope and the lease lookup in a short transaction.
    # The Manager has its own connection and must see this commit before it
    # executes its SELECT ... FOR UPDATE fencing checks.
    async with session_factory.begin() as session:
        environment = await session.scalar(
            select(SandboxEnvironmentModel).where(
                SandboxEnvironmentModel.owner_user_id == scope.owner_user_id
            )
        )
        lease = None
        if environment is not None:
            lease = await session.scalar(
                select(SandboxLeaseModel).where(
                    SandboxLeaseModel.environment_id == environment.id,
                    SandboxLeaseModel.auth_session_id == scope.auth_session_id,
                    SandboxLeaseModel.state == "active",
                )
            )
        if environment is None or lease is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Sandbox lease is not active.",
            )
        session.add(
            SandboxExecutionModel(
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
        )
        await session.flush()

    try:
        # Opening the event stream is part of the execution lifecycle.  If it
        # fails, the exception reaches the failure finalizer below and the row
        # is closed instead of remaining indefinitely in ``running``.
        await _emit_execution_event(
            request,
            execution_id=execution_id,
            user_id=scope.owner_user_id,
            event_type="execution.started",
            payload={},
        )
        result = await _sandbox_gateway(request).execute(scope, source=body.source, ticket=body.ticket)
        persisted_artifacts = await _finish_execution(
            request,
            session_factory,
            execution_id=execution_id,
            owner_user_id=scope.owner_user_id,
            status_value=str(result.get("status") or "completed"),
            result=result,
        )
        return {**result, "execution_id": execution_id, "artifacts": [{"id": artifact.id, "name": artifact.locator.rsplit("/", 1)[-1], "mime_type": artifact.mime_type} for artifact in persisted_artifacts]}
    except SandboxEventDeliveryError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    except PermissionError as error:
        try:
            await _finish_execution(
                request,
                session_factory,
                execution_id=execution_id,
                owner_user_id=scope.owner_user_id,
                status_value="failed",
                error=error,
            )
        except SandboxEventDeliveryError as delivery_error:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(delivery_error)) from delivery_error
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except Exception as error:
        try:
            await _finish_execution(
                request,
                session_factory,
                execution_id=execution_id,
                owner_user_id=scope.owner_user_id,
                status_value="failed",
                error=error,
            )
        except SandboxEventDeliveryError as delivery_error:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(delivery_error)) from delivery_error
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
