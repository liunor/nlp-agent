"""Authenticated Phase 0 sandbox lifecycle endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import (
    Principal,
    WriteClaims,
    get_database_session_claims,
    get_db_session,
)
from server.web.database_auth import DatabaseSessionClaims
from configs.settings import settings
from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig
from server.sandbox.gateway import SandboxGateway
from server.sandbox.manager import WarmPoolManager
from server.sandbox.ticket import SandboxTicketSigner
from server.sandbox.events import SandboxEventStore
from server.web.protocol import control_event

from .contracts import SandboxScope
from .service import sandbox_lifecycle_service
from .inmemory_runtime import InMemoryRuntime


router = APIRouter(prefix="/api/v1/sandbox", tags=["sandbox"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
DatabaseClaims = Annotated[DatabaseSessionClaims, Depends(get_database_session_claims)]
inmemory_runtime = InMemoryRuntime()
sandbox_events = SandboxEventStore()


class ExecuteBody(BaseModel):
    source: str = Field(min_length=1, max_length=20_000)
    ticket: str | None = Field(default=None, max_length=2_048)


class RuntimeTicketBody(BaseModel):
    ticket: str | None = Field(default=None, max_length=2_048)


def _sandbox_gateway(request: Request) -> SandboxGateway:
    existing = getattr(request.app.state, "sandbox_execution_gateway", None)
    if existing is not None:
        return existing
    factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
    if factory is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sandbox database is unavailable.")
    mode = settings.NLP_AGENT_SANDBOX_RUNTIME_MODE.strip().lower()
    secret = settings.NLP_AGENT_WEB_SECRET or "development-sandbox-ticket-secret"
    manager = None
    if mode == "docker":
        image = settings.NLP_AGENT_SANDBOX_DOCKER_IMAGE_DIGEST.strip()
        if not image:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sandbox Docker image is not configured.")
        manager = WarmPoolManager(
            session_factory=factory,
            docker=DockerRuntimeAdapter(DockerRuntimeConfig(image=image)),
            resource_profile_id="python-base",
            ready_target=max(1, settings.NLP_AGENT_SANDBOX_WARM_POOL_READY_TARGET),
        )
    if mode not in {"inmemory", "docker"}:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sandbox runtime is not enabled.")
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
    principal: Principal,
    claims: DatabaseClaims,
    _write_claims: WriteClaims,
) -> dict:
    scope = SandboxScope.from_authenticated_request(principal, claims)
    execution_id = str(uuid4())
    await sandbox_events.append(execution_id, user_id=scope.owner_user_id, event_type="execution.started", payload={})
    await request.app.state.hub.broadcast(control_event("sandbox.execution.started", payload={"execution_id": execution_id, "seq": 1}), user_id=scope.owner_user_id)
    try:
        result = await _sandbox_gateway(request).execute(scope, source=body.source, ticket=body.ticket)
        output = str(result.get("stdout") or result.get("stderr") or "")
        if output:
            event = await sandbox_events.append(execution_id, user_id=scope.owner_user_id, event_type="execution.output", payload={"text": output})
            await request.app.state.hub.broadcast(control_event("sandbox.execution.output", payload={"execution_id": execution_id, **event}), user_id=scope.owner_user_id)
        event = await sandbox_events.append(execution_id, user_id=scope.owner_user_id, event_type="execution.completed", payload={})
        await request.app.state.hub.broadcast(control_event("sandbox.execution.completed", payload={"execution_id": execution_id, **event}), user_id=scope.owner_user_id)
        return {**result, "execution_id": execution_id}
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error


@router.get("/executions/{execution_id}/events")
async def replay_execution_events(
    execution_id: str,
    principal: Principal,
    claims: DatabaseClaims,
    after_event_id: str | None = None,
) -> dict:
    scope = SandboxScope.from_authenticated_request(principal, claims)
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
