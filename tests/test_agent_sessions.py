"""Tests for agent session management service.

Uses mock AsyncSession to avoid requiring a real MySQL database.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.identity import AuthenticatedPrincipal
from server.agent_sessions.service import AgentSessionService, WorkspaceAccessDeniedError
from server.agent_sessions.schemas import AgentSessionCreate


def _principal(user_id: str = "user-1", workspaces: frozenset = frozenset({"default"})) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=user_id,
        workspace_ids=workspaces,
        roles=frozenset({"student"}),
    )


# ---------------------------------------------------------------------------
# AgentSessionService — list_sessions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_sessions_returns_empty() -> None:
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_count = MagicMock()
    mock_count.scalar.return_value = 0

    db = AsyncMock()
    # list_sessions calls scalars() then scalar() for count
    db.scalars = AsyncMock(return_value=mock_result)
    db.scalar = AsyncMock(return_value=0)

    service = AgentSessionService(db)
    principal = _principal()
    sessions, total = await service.list_sessions(principal)

    assert sessions == []
    assert total == 0


# ---------------------------------------------------------------------------
# AgentSessionService — get_session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_session_returns_none_when_missing() -> None:
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    service = AgentSessionService(db)
    principal = _principal()

    result = await service.get_session(principal, "nonexistent-session")
    assert result is None


# ---------------------------------------------------------------------------
# AgentSessionService — create_session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_session_adds_model() -> None:
    db = AsyncMock()
    db.flush = AsyncMock()

    service = AgentSessionService(db)
    principal = _principal()
    data = AgentSessionCreate(title="Test Session")

    session = await service.create_session(principal, data, workspace_id="default")

    db.add.assert_called_once()
    assert session.title == "Test Session"
    assert session.workspace_id == "default"
    assert session.created_by_user_id == "user-1"


# ---------------------------------------------------------------------------
# AgentSessionService — delete_session (soft delete)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_session_returns_false_when_not_found() -> None:
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    service = AgentSessionService(db)
    principal = _principal()

    result = await service.delete_session(principal, "nonexistent")
    assert result is False


# ---------------------------------------------------------------------------
# AgentSessionService — workspace isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_session_raises_when_no_workspace() -> None:
    db = AsyncMock()
    service = AgentSessionService(db)
    # Principal with no workspaces
    principal = AuthenticatedPrincipal(
        user_id="user-1",
        workspace_ids=frozenset(),
        roles=frozenset({"student"}),
    )
    data = AgentSessionCreate(title="Test")

    with pytest.raises(WorkspaceAccessDeniedError):
        await service.create_session(principal, data)
