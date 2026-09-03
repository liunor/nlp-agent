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
from server.rbac.service import UnknownRoleError, rbac_service

from server.auth.dependencies import Principal, WriteClaims, get_db_session

from .schemas import (
    PasswordChange,
    PasswordReset,
    UserAdminUpdate,
    UserCreateWithRole,
    UserListResponse,
    UserResponse,
    UserUpdate,
)
from .service import (
    SelfDeleteForbiddenError,
    UserAlreadyExistsError,
    UserNotFoundError,
    UserService,
    LastDeveloperForbiddenError,
)

router = APIRouter(prefix="/api/v1/users", tags=["users"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


async def _user_response_with_roles(
    service: UserService, user
) -> UserResponse:
    """Build a ``UserResponse`` including the user's role codes."""
    # Role replacement increments authorization_version, which also expires
    # SQLAlchemy's server-managed timestamp attributes. Refresh while we are
    # still inside the async session so Pydantic serialization never attempts
    # an implicit lazy load (which raises MissingGreenlet).
    await service.session.refresh(user)
    roles_map = await service.get_roles_for_users([user.id])
    return UserResponse.model_validate(user).model_copy(
        update={"roles": roles_map.get(user.id, [])}
    )


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
    roles_map = await service.get_roles_for_users([u.id for u in users])

    return UserListResponse(
        users=[
            UserResponse.model_validate(u).model_copy(
                update={"roles": roles_map.get(u.id, [])}
            )
            for u in users
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    data: UserCreateWithRole,
    db: DbSession,
    _write: WriteClaims,
    principal: Principal,
):
    """Create a new user account (admin only, review §8.2).

    Creates the user together with a personal workspace and assigns the
    creator as the workspace owner. The password is hashed server-side and
    is never returned in the response (review §7.2).

    ``role_codes`` is optional: without it the account keeps the default
    least-privilege ``guest`` role; when supplied, the roles are assigned
    through ``rbac_service.replace_user_roles`` so the full safety net
    (audit trail, Outbox event, session/sandbox invalidation, last-developer
    protection) applies.
    """
    authorization_service.require(principal, Permission.SYSTEM_USER_MANAGE)
    if data.role_codes:
        authorization_service.require(principal, Permission.SYSTEM_ROLE_MANAGE)

    service = UserService(db)
    try:
        user = await service.create_user(data, actor_user_id=principal.user_id)
        if data.role_codes:
            await rbac_service.replace_user_roles(
                db,
                user_id=user.id,
                role_codes=set(data.role_codes),
                assigned_by_user_id=principal.user_id,
            )
    except UserAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except UnknownRoleError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

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
    return await _user_response_with_roles(service, user)


@router.get("/me", response_model=UserResponse)
async def get_current_user(
    db: DbSession,
    principal: Principal,
):
    """Get current user's profile (including the real role codes).

    Roles are loaded from ``nlp_user_roles`` — an empty list here would make
    the frontend fall back to showing "游客", so it must reflect the DB.
    """
    service = UserService(db)
    try:
        user = await service.get_user(principal.user_id)
        return await _user_response_with_roles(service, user)
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
        return await _user_response_with_roles(service, user)
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
    await service.change_password(principal.user_id, data.new_password, user=user)
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
        return await _user_response_with_roles(service, user)
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail="User not found")
    except LastDeveloperForbiddenError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


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
        previous_display_name = user.display_name
        if data.display_name is not None:
            user.display_name = data.display_name
            if data.display_name != previous_display_name:
                await rbac_service.audit(
                    db,
                    actor_user_id=principal.user_id,
                    target_user_id=user_id,
                    decision="allow",
                    reason_code="user_display_name_updated",
                    permission_code="system:user:manage",
                    resource_type="user",
                    resource_id=user_id,
                    detail={
                        "before": previous_display_name,
                        "after": data.display_name,
                    },
                )
        previous_status = user.status
        if data.status is not None:
            await service.update_user_status(
                user_id, data.status, actor_user_id=principal.user_id
            )

            if data.status != previous_status:
                reason_code = {
                    "disabled": "user_account_disabled",
                    "active": "user_account_enabled",
                    "locked": "user_account_locked",
                }[data.status]
                await rbac_service.audit(
                    db,
                    actor_user_id=principal.user_id,
                    target_user_id=user_id,
                    decision="allow",
                    reason_code=reason_code,
                    permission_code="system:user:manage",
                    resource_type="user",
                    resource_id=user_id,
                    detail={"before": previous_status, "after": data.status},
                )

        await db.flush()
        return await _user_response_with_roles(service, user)
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail="User not found")
    except LastDeveloperForbiddenError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


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
    except LastDeveloperForbiddenError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


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
    except LastDeveloperForbiddenError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


# 角色分配统一由 ``PUT /api/v1/users/{user_id}/roles``（server/web/app.py 中的
# rbac_service.replace_user_roles）提供：它包含审计、Outbox 事件、沙箱租约失效、
# 以及"最后一个 developer"保护。此处不再注册同路径的简化版本，避免两套实现并存。


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
    except LastDeveloperForbiddenError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


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
        return await _user_response_with_roles(service, user)
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail="Deleted user not found")
    except LastDeveloperForbiddenError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


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
