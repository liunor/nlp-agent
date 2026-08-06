"""Tests for workspace management service.

Uses mock AsyncSession to avoid requiring a real MySQL database.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from server.workspace.service import WorkspaceService, WorkspaceNotFoundError


# ---------------------------------------------------------------------------
# WorkspaceService — list_user_workspaces
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_user_workspaces_returns_all() -> None:
    mock_ws = MagicMock()
    mock_ws.id = "ws-1"
    mock_ws.slug = "test-ws"
    mock_ws.name = "Test Workspace"
    mock_ws.status = "active"

    db = AsyncMock()
    db.scalars = AsyncMock(return_value=[mock_ws])

    service = WorkspaceService(db)
    workspaces = await service.list_user_workspaces("user-1")

    assert len(workspaces) == 1
    assert workspaces[0].slug == "test-ws"


# ---------------------------------------------------------------------------
# WorkspaceService — get_workspace
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_workspace_not_found() -> None:
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    service = WorkspaceService(db)

    with pytest.raises(WorkspaceNotFoundError):
        await service.get_workspace("nonexistent-ws")


# ---------------------------------------------------------------------------
# WorkspaceService — list_members
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_members_empty() -> None:
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=[])

    service = WorkspaceService(db)
    members = await service.list_members("ws-1")

    assert members == []


# ---------------------------------------------------------------------------
# WorkspaceService — remove_member
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remove_member_not_found() -> None:
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    service = WorkspaceService(db)
    result = await service.remove_member("ws-1", "user-1")

    assert result is False
