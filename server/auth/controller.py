"""Supplementary authentication endpoints.

The primary login/logout/session flow is handled by SameOriginSessionAuth
in server/web/app.py.  This controller only provides endpoints that app.py
does NOT cover:

  GET  /api/v1/auth/me              – current user profile + RBAC context
  POST /api/v1/auth/password        – change own password
  GET  /api/v1/auth/active-sessions – list own active DB sessions (admin)
  POST /api/v1/auth/revoke-sessions – revoke another user's sessions (admin)
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.identity import AuthenticatedPrincipal
from core.rbac import Permission, authorization_service

from server.auth.dependencies import get_current_principal, get_db_session
from server.auth.schemas import (
    ActiveSessionListResponse,
    MeResponse,
    PasswordChangeRequest,
    RevokeSessionsRequest,
    RevokeSessionsResponse,
)
from server.auth import roles_with_admin_alias
from server.user.service import UserService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]
Principal = Annotated[AuthenticatedPrincipal, Depends(get_current_principal)]


@router.get("/me", response_model=MeResponse)
async def get_current_user_profile(
    request: Request,
    db: DbSession,
    principal: Principal,
):
    """Return the authenticated user's profile with RBAC context.

    Combines DB user data with the HMAC session's role/workspace claims
    to give the frontend a complete identity snapshot.
    """
    user_service = UserService(db)
    try:
        user = await user_service.get_user(principal.user_id)
    except Exception:
        raise HTTPException(status_code=404, detail="User not found")

    return MeResponse(
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        status=user.status,
        roles=sorted(roles_with_admin_alias(principal.roles)),
        workspace_ids=sorted(principal.workspace_ids),
        permissions=sorted(principal.permissions),
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    data: PasswordChangeRequest,
    db: DbSession,
    principal: Principal,
):
    """Change the current user's password.

    Bumps authorization_version which invalidates all existing sessions.
    The frontend should re-authenticate after this call.
    """
    user_service = UserService(db)
    user = await user_service.get_user(principal.user_id)

    if not await user_service.verify_password(user, data.current_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    await user_service.change_password(principal.user_id, data.new_password)


@router.get("/active-sessions", response_model=ActiveSessionListResponse)
async def list_active_sessions(
    db: DbSession,
    principal: Principal,
):
    """List active DB-tracked sessions for the current user or all users (admin).

    This is supplementary to the HMAC in-memory sessions tracked by
    SameOriginSessionAuth.  DB sessions provide persistent audit evidence.
    """
    from server.auth.service import AuthService

    auth_service = AuthService(db)

    if authorization_service.allowed(principal, Permission.SYSTEM_USER_MANAGE):
        sessions = await auth_service.get_all_active_sessions()
    else:
        sessions = await auth_service.get_active_sessions_for_user(principal.user_id)

    return ActiveSessionListResponse(
        sessions=[
            {
                "id": s.id,
                "user_id": s.user_id,
                "workspace_id": s.workspace_id,
                "expires_at": s.expires_at,
                "revoked_at": s.revoked_at,
                "created_at": s.created_at,
            }
            for s in sessions
        ]
    )


@router.post("/revoke-sessions", response_model=RevokeSessionsResponse)
async def revoke_user_sessions(
    data: RevokeSessionsRequest,
    db: DbSession,
    principal: Principal,
):
    """Revoke all DB sessions for a user (admin operation).

    Also bumps authorization_version to force HMAC session re-validation.
    """
    authorization_service.require(principal, Permission.SYSTEM_USER_MANAGE)

    from server.auth.service import AuthService

    auth_service = AuthService(db)
    count = await auth_service.revoke_user_sessions(data.user_id)

    return RevokeSessionsResponse(revoked_count=count)
