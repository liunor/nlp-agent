"""Users controller for user management operations."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.orm import Session

from application.identity.authorization import AccessDeniedError
from application.identity.user_service import UserService
from schemas.user import (
    PasswordReset,
    UserAdminUpdate,
    UserCreate,
    UserListResponse,
    UserResponse,
    UserUpdate,
)
from server.web.dependencies.identity import DbSession, Principal

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("", response_model=UserListResponse)
async def list_users(
    db: DbSession,
    principal: Principal,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
):
    """List all users (admin only)."""
    principal.require_admin()

    user_service = UserService(db)
    users, total = user_service.list_users(offset=offset, limit=limit)

    return UserListResponse(
        items=[UserResponse.model_validate(u) for u in users],
        total=total,
    )


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    db: DbSession,
    principal: Principal,
):
    """Create a new user (admin only or first user setup)."""
    user_service = UserService(db)

    if not principal.is_admin:
        users, total = user_service.list_users(limit=1)
        if total > 0:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only admin can create users after initial setup",
            )

    try:
        user = user_service.create_user(
            username=body.username,
            password=body.password,
            display_name=body.display_name,
            email=body.email,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    if principal.is_admin:
        user_service.assign_role(user.id, "learner")

    return UserResponse.model_validate(user)


@router.get("/me", response_model=UserResponse)
async def get_current_user(
    db: DbSession,
    principal: Principal,
):
    """Get current user's profile."""
    user_service = UserService(db)
    user = user_service.get_user(principal.user_id)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return UserResponse.model_validate(user)


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    db: DbSession,
    principal: Principal,
):
    """Get user by ID (admin only or self)."""
    if principal.user_id != user_id:
        principal.require_admin()

    user_service = UserService(db)
    user = user_service.get_user(user_id)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return UserResponse.model_validate(user)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    body: UserUpdate,
    db: DbSession,
    principal: Principal,
):
    """Update user profile (self or admin)."""
    if principal.user_id != user_id:
        principal.require_admin()

    user_service = UserService(db)
    user = user_service.update_user(
        user_id,
        display_name=body.display_name,
        email=body.email,
    )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return UserResponse.model_validate(user)


@router.patch("/{user_id}/admin", response_model=UserResponse)
async def admin_update_user(
    user_id: str,
    body: UserAdminUpdate,
    db: DbSession,
    principal: Principal,
):
    """Admin update user (status, roles, etc.)."""
    principal.require_admin()

    user_service = UserService(db)

    if body.status == "disabled":
        user = user_service.disable_user(user_id)
    elif body.status == "active":
        user = user_service.enable_user(user_id)
    else:
        user = user_service.update_user(
            user_id,
            display_name=body.display_name,
            email=body.email,
        )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return UserResponse.model_validate(user)


@router.post("/{user_id}/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable_user(
    user_id: str,
    db: DbSession,
    principal: Principal,
):
    """Disable a user (admin only)."""
    principal.require_admin()

    user_service = UserService(db)
    user = user_service.disable_user(user_id)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )


@router.post("/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    user_id: str,
    body: PasswordReset,
    db: DbSession,
    principal: Principal,
):
    """Reset user's password (admin only)."""
    principal.require_admin()

    auth_service = __import__("application.identity.auth_service", fromlist=["AuthService"]).AuthService(db)
    success = auth_service.reset_password(user_id, body.new_password)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )


@router.get("/{user_id}/roles")
async def get_user_roles(
    user_id: str,
    db: DbSession,
    principal: Principal,
):
    """Get user's roles."""
    if principal.user_id != user_id:
        principal.require_admin()

    user_service = UserService(db)
    roles = user_service.get_user_roles(user_id)

    return {"user_id": user_id, "roles": roles}


@router.get("/{user_id}/workspaces")
async def get_user_workspaces(
    user_id: str,
    db: DbSession,
    principal: Principal,
):
    """Get user's workspaces."""
    if principal.user_id != user_id:
        principal.require_admin()

    user_service = UserService(db)
    workspaces = user_service.get_user_workspaces(user_id)

    return {"user_id": user_id, "workspaces": workspaces}
