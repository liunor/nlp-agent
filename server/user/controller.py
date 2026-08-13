"""User management API controller.

Provides REST endpoints for user CRUD operations.
Uses unified Principal/WriteClaims dependencies for auth + CSRF.
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.identity import AuthenticatedPrincipal
from core.rbac import Permission, authorization_service

from server.auth.dependencies import Principal, WriteClaims, get_db_session

from .schemas import (
    UserAdminUpdate,
    UserListResponse,
    UserResponse,
    UserUpdate,
)
from .service import UserService, UserNotFoundError, UserAlreadyExistsError

router = APIRouter(prefix="/api/v1/users", tags=["users"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("", response_model=UserListResponse)
async def list_users(
    db: DbSession,
    principal: Principal,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    status: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
):
    """List all users (admin only)."""
    authorization_service.require(principal, Permission.SYSTEM_USER_MANAGE)

    service = UserService(db)
    users, total = await service.list_users(offset=offset, limit=limit, status=status, keyword=keyword)

    return UserListResponse(
        users=[UserResponse.model_validate(u) for u in users],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user(
    db: DbSession,
    principal: Principal,
):
    """Get current user's profile."""
    service = UserService(db)
    try:
        user = await service.get_user(principal.user_id)
        return UserResponse.model_validate(user)
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail="User not found")


@router.patch("/me", response_model=UserResponse)
async def update_current_user(
    data: UserUpdate,
    db: DbSession,
    _write: WriteClaims,
    principal: Principal,
):
    """Update current user's profile (self-service)."""
    authorization_service.require(principal, Permission.IDENTITY_PROFILE_UPDATE_SELF)

    service = UserService(db)
    try:
        user = await service.update_user(principal.user_id, data)
        return UserResponse.model_validate(user)
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail="User not found")
    except UserAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    db: DbSession,
    principal: Principal,
):
    """Get user by ID (admin only)."""
    authorization_service.require(principal, Permission.SYSTEM_USER_MANAGE)

    service = UserService(db)
    try:
        user = await service.get_user(user_id)
        return UserResponse.model_validate(user)
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail="User not found")


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    data: UserAdminUpdate,
    db: DbSession,
    _write: WriteClaims,
    principal: Principal,
):
    """Update user profile (admin only)."""
    authorization_service.require(principal, Permission.SYSTEM_USER_MANAGE)

    service = UserService(db)
    try:
        user = await service.get_user(user_id)

        # Apply admin updates
        if data.display_name is not None:
            user.display_name = data.display_name
        if data.status is not None:
            await service.update_user_status(
                user_id, data.status, actor_user_id=principal.user_id
            )

        await db.flush()
        return UserResponse.model_validate(user)
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail="User not found")


@router.post("/{user_id}/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable_user(
    user_id: str,
    db: DbSession,
    _write: WriteClaims,
    principal: Principal,
):
    """Disable a user account (admin only)."""
    authorization_service.require(principal, Permission.SYSTEM_USER_MANAGE)

    service = UserService(db)
    try:
        await service.update_user_status(
            user_id, "disabled", actor_user_id=principal.user_id
        )
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail="User not found")


@router.post("/{user_id}/enable", status_code=status.HTTP_204_NO_CONTENT)
async def enable_user(
    user_id: str,
    db: DbSession,
    _write: WriteClaims,
    principal: Principal,
):
    """Enable a disabled user account (admin only)."""
    authorization_service.require(principal, Permission.SYSTEM_USER_MANAGE)

    service = UserService(db)
    try:
        await service.update_user_status(
            user_id, "active", actor_user_id=principal.user_id
        )
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail="User not found")
