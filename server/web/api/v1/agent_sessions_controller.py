"""Agent sessions controller for session management operations."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.orm import Session

from application.agent_sessions.service import AgentSessionService
from application.turns.module import TurnApplication
from schemas.agent_session import (
    AgentSessionCreate,
    AgentSessionListResponse,
    AgentSessionResponse,
    AgentSessionUpdate,
)
from schemas.turn import TurnEventListResponse, TurnListResponse, TurnResponse
from server.web.dependencies.identity import DbSession, Principal

router = APIRouter(prefix="/api/v1/agent-sessions", tags=["agent-sessions"])


@router.get("", response_model=AgentSessionListResponse)
async def list_sessions(
    db: DbSession,
    principal: Principal,
    status_filter: str = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """List agent sessions in the current workspace."""
    session_service = AgentSessionService(db)
    sessions = session_service.list_sessions(
        principal,
        status=status_filter,
        limit=limit,
        offset=offset,
    )

    return AgentSessionListResponse(
        items=[AgentSessionResponse.model_validate(s) for s in sessions],
    )


@router.post("", response_model=AgentSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: AgentSessionCreate,
    db: DbSession,
    principal: Principal,
):
    """Create a new agent session."""
    session_service = AgentSessionService(db)

    session = session_service.create_session(
        principal,
        title=body.title,
        model_profile_id=body.model_profile_id,
        metadata=body.metadata,
    )

    return AgentSessionResponse.model_validate(session)


@router.get("/{session_id}", response_model=AgentSessionResponse)
async def get_session(
    session_id: str,
    db: DbSession,
    principal: Principal,
):
    """Get agent session by ID."""
    session_service = AgentSessionService(db)
    session = session_service.get_session(principal, session_id)

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    return AgentSessionResponse.model_validate(session)


@router.patch("/{session_id}", response_model=AgentSessionResponse)
async def update_session(
    session_id: str,
    body: AgentSessionUpdate,
    db: DbSession,
    principal: Principal,
):
    """Update agent session."""
    session_service = AgentSessionService(db)

    try:
        session = session_service.update_session(
            principal,
            session_id,
            title=body.title,
            status=body.status,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    return AgentSessionResponse.model_validate(session)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    db: DbSession,
    principal: Principal,
):
    """Delete agent session."""
    session_service = AgentSessionService(db)

    try:
        success = session_service.delete_session(principal, session_id)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )


@router.get("/{session_id}/turns", response_model=TurnListResponse)
async def list_turns(
    session_id: str,
    db: DbSession,
    principal: Principal,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """List turns for a session."""
    turn_app = TurnApplication(db)

    try:
        turns = turn_app.list_turns(
            principal, session_id, limit=limit, offset=offset
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    return TurnListResponse(
        items=[TurnResponse.model_validate(t) for t in turns],
    )


@router.get("/{session_id}/turns/{turn_id}/events", response_model=TurnEventListResponse)
async def get_turn_events(
    session_id: str,
    turn_id: str,
    db: DbSession,
    principal: Principal,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
):
    """Get events for a turn."""
    turn_app = TurnApplication(db)

    try:
        events = turn_app.get_events(
            principal,
            turn_id,
            after_sequence=after_sequence,
            limit=limit,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Turn not found",
        )

    return TurnEventListResponse(
        items=[
            {
                "id": e.id,
                "turn_id": e.turn_id,
                "sequence": e.sequence,
                "type": e.type,
                "idempotency_key": e.idempotency_key,
                "payload": e.payload,
                "created_at": e.created_at,
            }
            for e in events
        ],
    )
