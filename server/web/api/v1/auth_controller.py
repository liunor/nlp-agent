"""Authentication controller for login/logout operations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from application.identity.auth_service import AuthService, AuthenticationError
from schemas.auth import LoginRequest, LoginResponse, SessionInfo
from server.web.dependencies.identity import DbSession, Principal

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    response: Response,
    db: DbSession,
):
    """Authenticate user and create a new session.

    Sets an HttpOnly cookie with the session token.
    """
    auth_service = AuthService(db)

    try:
        result = auth_service.login(
            username=body.username,
            password=body.password,
        )
    except AuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )

    response.set_cookie(
        key="nlp_session",
        value=result.session_token,
        max_age=86400,
        httponly=True,
        samesite="lax",
        path="/",
    )

    return LoginResponse(
        user_id=result.user.id,
        username=result.user.username,
        display_name=result.user.display_name,
        workspace_ids=result.workspace_ids,
        roles=result.system_roles,
        csrf_token=result.csrf_token,
        expires_at=result.expires_at,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    db: DbSession,
    principal: Principal,
):
    """Logout and revoke the current session."""
    auth_service = AuthService(db)
    auth_service.logout(principal.auth_session_id)

    response.delete_cookie("nlp_session", path="/", samesite="lax")


@router.get("/me", response_model=SessionInfo)
async def get_current_session(
    db: DbSession,
    principal: Principal,
):
    """Get current session information."""
    auth_service = AuthService(db)
    result = auth_service.validate_session(session_token="")

    return SessionInfo(
        user_id=principal.user_id,
        username="",
        display_name="",
        workspace_ids=[principal.workspace_id],
        roles=list(principal.system_roles),
        csrf_token="",
        expires_at=None,
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: dict,
    db: DbSession,
    principal: Principal,
):
    """Change current user's password."""
    from schemas.user import PasswordChange

    change = PasswordChange(**body)
    auth_service = AuthService(db)

    success = auth_service.change_password(
        principal.user_id,
        current_password=change.current_password,
        new_password=change.new_password,
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
