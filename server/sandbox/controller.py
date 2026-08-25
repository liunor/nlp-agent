"""Authenticated Phase 0 sandbox lifecycle endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import (
    Principal,
    WriteClaims,
    get_database_session_claims,
    get_db_session,
)
from server.web.database_auth import DatabaseSessionClaims

from .contracts import SandboxScope
from .service import sandbox_lifecycle_service


router = APIRouter(prefix="/api/v1/sandbox", tags=["sandbox"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
DatabaseClaims = Annotated[DatabaseSessionClaims, Depends(get_database_session_claims)]


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
