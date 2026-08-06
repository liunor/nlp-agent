"""Agent sessions API controller.

Manages agent session lifecycle. Turn execution endpoints are
provided by the existing gateway infrastructure in server/web/app.py.
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.identity import AuthenticatedPrincipal
from core.rbac import Permission, authorization_service

from server.auth.dependencies import Principal, WriteClaims, get_db_session

from .schemas import (
    AgentSessionCreate,
    AgentSessionListResponse,
    AgentSessionResponse,
    AgentSessionUpdate,
)
from .service import (
    AgentSessionService,
    AgentSessionNotFoundError,
    WorkspaceAccessDeniedError,
)

router = APIRouter(prefix="/api/v1/agent-sessions", tags=["agent-sessions"])


DbSession = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("", response_model=AgentSessionListResponse)
async def list_sessions(
    db: DbSession,
    principal: Principal,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List agent sessions accessible to the current user."""
    service = AgentSessionService(db)
    sessions, total = await service.list_sessions(
        principal,
        status=status_filter,
        limit=limit,
        offset=offset,
    )

    return AgentSessionListResponse(
        sessions=[AgentSessionResponse.model_validate(s) for s in sessions],
        total=total,
    )


@router.post("", response_model=AgentSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    data: AgentSessionCreate,
    db: DbSession,
    _write: WriteClaims,
    principal: Principal,
):
    """Create a new agent session."""
    authorization_service.require(principal, Permission.AGENT_SESSION_CREATE)

    service = AgentSessionService(db)
    try:
        session = await service.create_session(principal, data)
        return AgentSessionResponse.model_validate(session)
    except WorkspaceAccessDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.get("/{session_id}", response_model=AgentSessionResponse)
async def get_session(
    session_id: str,
    db: DbSession,
    principal: Principal,
):
    """Get agent session details."""
    authorization_service.require(principal, Permission.AGENT_SESSION_READ)

    service = AgentSessionService(db)
    session = await service.get_session(principal, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    return AgentSessionResponse.model_validate(session)


@router.patch("/{session_id}", response_model=AgentSessionResponse)
async def update_session(
    session_id: str,
    data: AgentSessionUpdate,
    db: DbSession,
    _write: WriteClaims,
    principal: Principal,
):
    """Update an agent session."""
    authorization_service.require(principal, Permission.AGENT_SESSION_UPDATE)

    service = AgentSessionService(db)
    try:
        session = await service.update_session(
            principal,
            session_id,
            title=data.title,
            status=data.status,
        )
        return AgentSessionResponse.model_validate(session)
    except AgentSessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except WorkspaceAccessDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    db: DbSession,
    _write: WriteClaims,
    principal: Principal,
):
    """Soft delete an agent session."""
    authorization_service.require(principal, Permission.AGENT_SESSION_DELETE)

    service = AgentSessionService(db)
    try:
        deleted = await service.delete_session(principal, session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Session not found")
    except WorkspaceAccessDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
