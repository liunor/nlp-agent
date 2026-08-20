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
from server.rbac.service import rbac_service

from server.auth.dependencies import Principal, WriteClaims, get_db_session

from .schemas import (
    PasswordChange,
    PasswordReset,
    UserAdminUpdate,
    UserCreate,
    UserListResponse,
    UserResponse,
    UserUpdate,
)
from .service import (
    SelfDeleteForbiddenError,
    UserAlreadyExistsError,
    UserNotFoundError,
    UserService,
)

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
    include_deleted: bool = Query(False),
):
    """List all users (admin only)."""
    authorization_service.require(principal, Permission.SYSTEM_USER_MANAGE)

    service = UserService(db)
    users, total = await service.list_users(
        offset=offset,
        limit=limit,
        status=status,
        keyword=keyword,
        include_deleted=include_deleted or status == "deleted",
    )

    return UserListResponse(
        users=[UserResponse.model_validate(u) for u in users],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    data: UserCreate,
    db: DbSession,
    _write: WriteClaims,
    principal: Principal,
):
    """Create a new user account (admin only, review §8.2).

    Creates the user together with a personal workspace and assigns the
    creator as the workspace owner. The password is hashed server-side and
    is never returned in the response (review §7.2).
    """
    authorization_service.require(principal, Permission.SYSTEM_USER_MANAGE)

    service = UserService(db)
    try:
        user = await service.create_user(data, actor_user_id=principal.user_id)
    except UserAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))

    # 高危账号操作写入审计事件
    await rbac_service.audit(
        db,
        actor_user_id=principal.user_id,
        target_user_id=user.id,
        decision="allow",
        reason_code="user_account_created",
        permission_code="system:user:manage",
        resource_type="user",
        resource_id=user.id,
    )
    return UserResponse.model_validate(user)


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


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_own_password(
    data: PasswordChange,
    db: DbSession,
    _write: WriteClaims,
    principal: Principal,
):
    """Change the current user's own password (self-service).

    Requires the existing password (review §6.1: old-password verification) and
    writes an audit event. Changing the password invalidates all existing
    sessions (review §8.1).
    """
    authorization_service.require(principal, Permission.IDENTITY_PROFILE_UPDATE_SELF)

    service = UserService(db)
    user = await service.get_user(principal.user_id)
    if not await service.verify_password(user, data.current_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )
    await service.change_password(principal.user_id, data.new_password)
    # 高危账号操作写入审计事件
    await rbac_service.audit(
        db,
        actor_user_id=principal.user_id,
        target_user_id=principal.user_id,
        decision="allow",
        reason_code="user_password_changed_self",
        permission_code="identity:profile:update_self",
        resource_type="user",
        resource_id=principal.user_id,
    )


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
        # 高危账号操作写入审计事件
        await rbac_service.audit(
            db,
            actor_user_id=principal.user_id,
            target_user_id=user_id,
            decision="allow",
            reason_code="user_account_disabled",
            permission_code="system:user:manage",
            resource_type="user",
            resource_id=user_id,
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
        # 高危账号操作写入审计事件
        await rbac_service.audit(
            db,
            actor_user_id=principal.user_id,
            target_user_id=user_id,
            decision="allow",
            reason_code="user_account_enabled",
            permission_code="system:user:manage",
            resource_type="user",
            resource_id=user_id,
        )
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail="User not found")


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    db: DbSession,
    _write: WriteClaims,
    principal: Principal,
):
    """Soft-delete a user account (admin only)."""
    authorization_service.require(principal, Permission.SYSTEM_USER_MANAGE)

    service = UserService(db)
    try:
        await service.soft_delete_user(
            user_id, actor_user_id=principal.user_id
        )
        # 高危账号操作写入审计事件
        await rbac_service.audit(
            db,
            actor_user_id=principal.user_id,
            target_user_id=user_id,
            decision="allow",
            reason_code="user_account_soft_deleted",
            permission_code="system:user:manage",
            resource_type="user",
            resource_id=user_id,
        )
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail="User not found")
    except SelfDeleteForbiddenError:
        raise HTTPException(status_code=403, detail="Cannot delete your own account")


@router.post("/{user_id}/restore", response_model=UserResponse)
async def restore_user(
    user_id: str,
    db: DbSession,
    _write: WriteClaims,
    principal: Principal,
):
    """Restore a soft-deleted account; old credentials remain unusable."""
    authorization_service.require(principal, Permission.SYSTEM_USER_MANAGE)
    service = UserService(db)
    try:
        user = await service.restore_user(user_id, actor_user_id=principal.user_id)
        await rbac_service.audit(
            db,
            actor_user_id=principal.user_id,
            target_user_id=user_id,
            decision="allow",
            reason_code="user_account_restored",
            permission_code="system:user:manage",
            resource_type="user",
            resource_id=user_id,
        )
        return UserResponse.model_validate(user)
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail="Deleted user not found")


@router.post("/{user_id}/sessions/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_user_sessions(
    user_id: str,
    db: DbSession,
    _write: WriteClaims,
    principal: Principal,
):
    """Revoke all active sessions for a user (admin only, P1-3).

    Admin-only session revocation with a dedicated audit trail. It does NOT
    reuse the user's own session endpoint, so a caller can only revoke the
    explicitly targeted account — never another user's sessions via a guessed
    session id (review P1-3).
    """
    authorization_service.require(principal, Permission.SYSTEM_USER_MANAGE)

    service = UserService(db)
    try:
        await service.revoke_user_sessions(user_id, actor_user_id=principal.user_id)
        # 高危账号操作写入审计事件
        await rbac_service.audit(
            db,
            actor_user_id=principal.user_id,
            target_user_id=user_id,
            decision="allow",
            reason_code="user_sessions_revoked",
            permission_code="system:user:manage",
            resource_type="user_session",
            resource_id=user_id,
        )
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail="User not found")


@router.post("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_password(
    user_id: str,
    data: PasswordReset,
    db: DbSession,
    _write: WriteClaims,
    principal: Principal,
):
    """Reset another user's password (admin only, review §6.1/§8.2).

    Gated by ``SYSTEM_USER_MANAGE`` so a non-admin (e.g. teacher) cannot reset
    another user's password — this is the §10 cross-user password-reset
    failure path. Resets invalidate the target's existing sessions (§8.1).
    """
    authorization_service.require(principal, Permission.SYSTEM_USER_MANAGE)

    service = UserService(db)
    try:
        await service.change_password(user_id, data.new_password)
        # 高危账号操作写入审计事件
        await rbac_service.audit(
            db,
            actor_user_id=principal.user_id,
            target_user_id=user_id,
            decision="allow",
            reason_code="user_password_reset_admin",
            permission_code="system:user:manage",
            resource_type="user",
            resource_id=user_id,
        )
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail="User not found")
