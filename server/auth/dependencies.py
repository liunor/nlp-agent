"""FastAPI dependencies for authentication and authorization.

Bridges the vertical modules (user, workspace, agent_sessions) with the
existing SameOriginSessionAuth from server/web/auth.py.

Exports:
  - get_db_session: AsyncSession dependency from the gateway's session factory
  - get_current_principal: Validates HMAC cookie + resolves RBAC principal
  - get_write_access: CSRF + origin validation for state-changing requests
  - Principal / WriteClaims: Annotated type aliases for controller use
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.identity import AuthenticatedPrincipal, AccessDeniedError


# ---------------------------------------------------------------------------
# Database session
# ---------------------------------------------------------------------------

async def get_db_session(request: Request) -> AsyncSession:
    """Yield an async database session from the gateway's session factory."""
    factory = getattr(
        request.app.state.gateway, "authorization_session_factory", None
    )
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not available",
        )
    async with factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Read access (authentication only)
# ---------------------------------------------------------------------------

async def get_current_principal(
    request: Request,
) -> AuthenticatedPrincipal:
    """Extract and validate the authenticated principal from the request.

    Uses the existing SameOriginSessionAuth from app.state to validate
    the session cookie, then resolves the full principal (with RBAC
    roles and workspace memberships) via the gateway's session factory.
    """
    auth = request.app.state.auth
    token = request.cookies.get(auth.cookie_name)

    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Cookie"},
        )

    try:
        claims = auth.authenticate(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
            headers={"WWW-Authenticate": "Cookie"},
        )

    # Guest users get a lightweight principal without DB lookup
    if claims.roles == frozenset({"guest"}):
        return claims.principal()

    # Resolve full principal with RBAC roles and workspace memberships
    from server.rbac.service import rbac_service

    session_factory = getattr(
        request.app.state.gateway, "authorization_session_factory", None
    )
    if session_factory is None:
        return claims.principal()

    async with session_factory() as session:
        return await rbac_service.principal_for_username(session, claims.user_id)


# ---------------------------------------------------------------------------
# Write access (CSRF + origin validation)
# ---------------------------------------------------------------------------

async def get_write_access(
    request: Request,
    claims: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    csrf_token: Annotated[Optional[str], Header(alias="X-CSRF-Token")] = None,
) -> AuthenticatedPrincipal:
    """Validate CSRF token and origin for state-changing requests.

    Mirrors the write_access dependency in server/web/app.py so that
    new module controllers enforce the same security checks.
    """
    auth = request.app.state.auth
    token = request.cookies.get(auth.cookie_name)

    try:
        session_claims = auth.authenticate(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )

    # Origin check
    auth.require_same_origin(
        request.headers.get("origin"), request.headers.get("host")
    )

    # CSRF check
    auth.require_csrf(session_claims, csrf_token)

    return claims


async def get_optional_principal(
    request: Request,
) -> Optional[AuthenticatedPrincipal]:
    """Extract principal if authenticated, otherwise return None."""
    try:
        return await get_current_principal(request)
    except HTTPException:
        return None


async def require_workspace_access(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    workspace_id: str,
) -> AuthenticatedPrincipal:
    """Require that the principal has access to the specified workspace."""
    try:
        principal.require_workspace(workspace_id)
    except AccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to workspace",
        )
    return principal


# ---------------------------------------------------------------------------
# Type aliases for controller use
# ---------------------------------------------------------------------------

Principal = Annotated[AuthenticatedPrincipal, Depends(get_current_principal)]
WriteClaims = Annotated[AuthenticatedPrincipal, Depends(get_write_access)]
