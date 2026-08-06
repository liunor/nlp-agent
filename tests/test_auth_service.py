"""Tests for auth service (HMAC bridge / DB session management).

Uses mock AsyncSession to avoid requiring a real MySQL database.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from server.auth.service import AuthService, hash_token


# ---------------------------------------------------------------------------
# hash_token
# ---------------------------------------------------------------------------

def test_hash_token_produces_consistent_sha256() -> None:
    h1 = hash_token("test-token")
    h2 = hash_token("test-token")
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex digest


def test_hash_token_differs_for_different_inputs() -> None:
    assert hash_token("token-a") != hash_token("token-b")


# ---------------------------------------------------------------------------
# AuthService — get_all_active_sessions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_all_active_sessions_returns_empty() -> None:
    mock_scalars_result = MagicMock()
    mock_scalars_result.__iter__ = MagicMock(return_value=iter([]))

    db = AsyncMock()
    db.scalars = AsyncMock(return_value=mock_scalars_result)

    service = AuthService(db)
    sessions = await service.get_all_active_sessions()

    assert sessions == []


# ---------------------------------------------------------------------------
# AuthService — get_active_sessions_for_user
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_active_sessions_for_user_returns_empty() -> None:
    mock_scalars_result = MagicMock()
    mock_scalars_result.__iter__ = MagicMock(return_value=iter([]))

    db = AsyncMock()
    db.scalars = AsyncMock(return_value=mock_scalars_result)

    service = AuthService(db)
    sessions = await service.get_active_sessions_for_user("user-1")

    assert sessions == []


# ---------------------------------------------------------------------------
# AuthService — revoke_user_sessions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_revoke_user_sessions_returns_zero_when_none_active() -> None:
    mock_result = MagicMock()
    mock_result.rowcount = 0

    db = AsyncMock()
    db.execute = AsyncMock(return_value=mock_result)
    db.flush = AsyncMock()

    service = AuthService(db)
    count = await service.revoke_user_sessions("user-1")

    assert count == 0


# ---------------------------------------------------------------------------
# AuthService — record_login_session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_login_session_adds_row() -> None:
    db = AsyncMock()
    db.flush = AsyncMock()

    service = AuthService(db)
    result = await service.record_login_session(
        user_id="user-1",
        workspace_id="default",
        token="test-token",
        csrf_hash="test-csrf",
    )

    db.add.assert_called_once()
    assert result.user_id == "user-1"
    assert result.workspace_id == "default"


# ---------------------------------------------------------------------------
# AuthService — build_principal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_principal_delegates_to_rbac() -> None:
    mock_user = MagicMock()
    mock_user.id = "user-1"

    mock_principal = MagicMock()
    mock_principal.user_id = "user-1"

    db = AsyncMock()

    service = AuthService(db)
    # Mock the rbac_service.principal_for_user_id
    service.rbac_service.principal_for_user_id = AsyncMock(return_value=mock_principal)

    result = await service.build_principal(mock_user)
    assert result.user_id == "user-1"
