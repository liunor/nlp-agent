"""Authenticated Phase 0 sandbox lifecycle endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
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

from .contracts import SandboxScope
from .service import sandbox_lifecycle_service
from .inmemory_runtime import InMemoryRuntime


router = APIRouter(prefix="/api/v1/sandbox", tags=["sandbox"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
DatabaseClaims = Annotated[DatabaseSessionClaims, Depends(get_database_session_claims)]
inmemory_runtime = InMemoryRuntime()


class ExecuteBody(BaseModel):
    source: str = Field(min_length=1, max_length=20_000)


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
    db: DbSession,
    principal: Principal,
    claims: DatabaseClaims,
    _write_claims: WriteClaims,
) -> dict:
    return await sandbox_lifecycle_service.ensure_current_lease(
        db, SandboxScope.from_authenticated_request(principal, claims)
    )


@router.post("/execute")
async def execute_sandbox(
    body: ExecuteBody,
    principal: Principal,
    claims: DatabaseClaims,
    _write_claims: WriteClaims,
) -> dict:
    if settings.NLP_AGENT_SANDBOX_RUNTIME_MODE != "inmemory":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sandbox execution is not enabled until an isolated runtime is configured.",
        )
    scope = SandboxScope.from_authenticated_request(principal, claims)
    return await inmemory_runtime.execute(user_id=scope.owner_user_id, source=body.source)


@router.post("/restart")
async def restart_sandbox(
    principal: Principal,
    claims: DatabaseClaims,
    _write_claims: WriteClaims,
) -> dict:
    if settings.NLP_AGENT_SANDBOX_RUNTIME_MODE != "inmemory":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sandbox execution is not enabled until an isolated runtime is configured.",
        )
    scope = SandboxScope.from_authenticated_request(principal, claims)
    await inmemory_runtime.restart(user_id=scope.owner_user_id)
    return {"status": "restarted"}
