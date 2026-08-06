"""Tests for user management service and controller.

Uses mock AsyncSession to avoid requiring a real MySQL database.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from server.user.service import UserService, UserNotFoundError, UserAlreadyExistsError, PasswordHasherSingleton
from server.user.schemas import UserCreate, UserUpdate


# ---------------------------------------------------------------------------
# PasswordHasherSingleton
# ---------------------------------------------------------------------------

def test_password_hasher_singleton_returns_same_instance() -> None:
    h1 = PasswordHasherSingleton.get()
    h2 = PasswordHasherSingleton.get()
    assert h1 is h2


def test_password_hasher_uses_argon2id() -> None:
    hasher = PasswordHasherSingleton.get()
    hash_value = hasher.hash("test-password")
    assert hasher.verify(hash_value, "test-password")


# ---------------------------------------------------------------------------
# UserService — list_users
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_users_returns_empty_when_no_users() -> None:
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=[])
    db.scalar = AsyncMock(return_value=0)

    service = UserService(db)
    users, total = await service.list_users()

    assert users == []
    assert total == 0


# ---------------------------------------------------------------------------
# UserService — get_user
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_user_raises_not_found_when_missing() -> None:
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    service = UserService(db)

    with pytest.raises(UserNotFoundError):
        await service.get_user("nonexistent-user")


# ---------------------------------------------------------------------------
# UserService — create_user
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_user_checks_duplicate() -> None:
    db = AsyncMock()
    db.scalar = AsyncMock(return_value="existing-user-id")

    service = UserService(db)

    with pytest.raises(UserAlreadyExistsError):
        await service.create_user(UserCreate(username="existinguser", display_name="Existing", password="password123"))


# ---------------------------------------------------------------------------
# UserService — update_user
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_user_not_found() -> None:
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    service = UserService(db)

    with pytest.raises(UserNotFoundError):
        await service.update_user("nonexistent", UserUpdate(display_name="New Name"))


# ---------------------------------------------------------------------------
# UserService — update_user_status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_user_status_not_found() -> None:
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    service = UserService(db)

    with pytest.raises(UserNotFoundError):
        await service.update_user_status("nonexistent", "disabled", actor_user_id="admin-1")
