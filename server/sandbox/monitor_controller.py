from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.identity import AuthenticatedPrincipal
from core.rbac import Permission, authorization_service

from .monitoring import (
    drain_runtime,
    get_runtime,
    list_executions,
    list_runtimes,
    preload_compatibility,
    replay_execution_events,
    request_capacity_prewarm,
    sandbox_logs,
    sandbox_overview,
)


class PrewarmBody(BaseModel):
    expected_sessions: int = Field(ge=0, le=100_000)
    sessions_per_runtime: int = Field(default=1, ge=1, le=100)
    profile_id: str = Field(default="python-base", min_length=1, max_length=64)
    execute_at: datetime | None = None


def create_sandbox_monitor_router(
    *,
    db_session_dependency: Callable[..., Any],
    principal_dependency: Callable[..., Any],
    write_access_dependency: Callable[..., Any],
) -> APIRouter:
    """Build routes with the monitor process' own auth and DB dependencies."""

    router = APIRouter(prefix="/api/v1/observability/sandbox", tags=["sandbox-monitor"])

    def require_monitor(identity: AuthenticatedPrincipal) -> None:
        authorization_service.require(identity, Permission.SYSTEM_RUNTIME_MONITOR)

    @router.get("/overview")
    async def overview(
        request: Request,
        db: AsyncSession = Depends(db_session_dependency),
        identity: AuthenticatedPrincipal = Depends(principal_dependency),
    ):
        require_monitor(identity)
        return await sandbox_overview(db, request)

    @router.get("/logs")
    async def logs(
        db: AsyncSession = Depends(db_session_dependency),
        identity: AuthenticatedPrincipal = Depends(principal_dependency),
        limit: int = 80,
        since_seconds: int = 600,
    ):
        require_monitor(identity)
        if not 1 <= limit <= 200 or not 60 <= since_seconds <= 3600:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid log window.")
        return await sandbox_logs(db, limit=limit, since_seconds=since_seconds)

    @router.get("/runtimes")
    async def runtimes(
        db: AsyncSession = Depends(db_session_dependency),
        identity: AuthenticatedPrincipal = Depends(principal_dependency),
    ):
        require_monitor(identity)
        return {"items": await list_runtimes(db)}

    @router.get("/runtimes/{runtime_id}")
    async def runtime(
        runtime_id: str,
        db: AsyncSession = Depends(db_session_dependency),
        identity: AuthenticatedPrincipal = Depends(principal_dependency),
    ):
        require_monitor(identity)
        result = await get_runtime(db, runtime_id)
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sandbox runtime not found.")
        return result

    @router.post("/runtimes/{runtime_id}/drain")
    async def drain(
        runtime_id: str,
        db: AsyncSession = Depends(db_session_dependency),
        identity: AuthenticatedPrincipal = Depends(principal_dependency),
        _write: Any = Depends(write_access_dependency),
    ):
        require_monitor(identity)
        try:
            return await drain_runtime(db, runtime_id, identity)
        except LookupError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    @router.get("/executions")
    async def executions(
        db: AsyncSession = Depends(db_session_dependency),
        identity: AuthenticatedPrincipal = Depends(principal_dependency),
        status_filter: str | None = None,
    ):
        require_monitor(identity)
        return {"items": await list_executions(db, status_filter=status_filter)}

    @router.get("/executions/{execution_id}/events")
    async def execution_events(
        execution_id: str,
        db: AsyncSession = Depends(db_session_dependency),
        identity: AuthenticatedPrincipal = Depends(principal_dependency),
        after_event_id: str | None = None,
    ):
        require_monitor(identity)
        try:
            return await replay_execution_events(db, execution_id, after_event_id=after_event_id)
        except LookupError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    @router.get("/preload-compatibility")
    async def preload(identity: AuthenticatedPrincipal = Depends(principal_dependency)):
        require_monitor(identity)
        return await preload_compatibility()

    @router.post("/capacity/prewarm")
    async def prewarm(
        body: PrewarmBody,
        identity: AuthenticatedPrincipal = Depends(principal_dependency),
        _write: Any = Depends(write_access_dependency),
    ):
        require_monitor(identity)
        try:
            return await request_capacity_prewarm(body, identity)
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error

    return router
