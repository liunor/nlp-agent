"""FastAPI auth dependencies for the user/workspace/classroom_join modules.

Bridges the vertical modules with V3's existing ``SameOriginSessionAuth``
(``server/web/auth.py``), mirroring the closures inside ``create_app`` so the
new module controllers enforce the same authentication + CSRF + same-origin
checks as the rest of the API.

Exports:
  - get_db_session: AsyncSession dependency from the gateway's session factory
  - get_current_principal: cookie auth + RBAC principal resolution
  - get_write_access: CSRF + origin validation for state-changing requests
  - Principal / WriteClaims: Annotated type aliases for controller use
"""

from __future__ import annotations

from typing import Annotated, AsyncIterator

from fastapi import Depends, Header, Request, Security
from sqlalchemy.ext.asyncio import AsyncSession

from core.identity import AuthenticatedPrincipal
from server.rbac.service import rbac_service
from server.web.auth import (
    AuthenticationError,
    CsrfRejectedError,
    OriginRejectedError,
    SameOriginSessionAuth,
    SessionClaims,
)
from server.web.database_auth import DatabaseSessionClaims


async def _claims(request: Request) -> SessionClaims | DatabaseSessionClaims:
    """Authenticate the session cookie; raises AuthenticationError on failure."""
    auth: SameOriginSessionAuth = request.app.state.auth
    token = request.cookies.get(auth.cookie_name)
    if not getattr(request.app.state, "auth_injected", False):
        factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
        if factory is None:
            raise AuthenticationError("database authentication is unavailable")
        database_auth = request.app.state.database_auth
        return await database_auth.authenticate(factory, token)
    return auth.authenticate(token)


async def get_database_session_claims(
    claims: Annotated[SessionClaims | DatabaseSessionClaims, Depends(_claims)],
) -> DatabaseSessionClaims:
    """Require the persistent user session used by sandbox ownership.

    The legacy injected authentication mode carries a mutable username rather
    than a database user UUID, so it cannot be used as a sandbox owner.
    """
    if not isinstance(claims, DatabaseSessionClaims):
        raise AuthenticationError("database authentication is required for sandbox access")
    return claims


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield an async database session bound to a single request-scoped transaction.

    The session is opened from the gateway's session factory and wrapped in an
    explicit ``session.begin()`` so that every write performed during the request
    is committed together on success and rolled back on error — one request, one
    transaction.

    This fixes review P1-1: previously the factory only flushed (the
    ``async_sessionmaker`` is created with ``autoflush=False`` and the dependency
    never committed), so writes in ``server/user`` and ``server/workspace`` —
    and the audit events added in 阶段3 — never reached the database. Only
    ``server/classroom_join`` committed inline, producing the "sometimes
    committed, sometimes not" inconsistency the review flagged.
    """
    factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
    if factory is None:
        raise RuntimeError("RBAC persistence requires MySQL")
    async with factory() as session:
        async with session.begin():
            yield session


async def get_current_principal(
    request: Request,
    claims: Annotated[SessionClaims | DatabaseSessionClaims, Depends(_claims)],
) -> AuthenticatedPrincipal:
    """Resolve roles/workspace/ classroom membership from MySQL.

    Mirrors ``resolve_principal`` in ``server/web/app.py``: guests get a
    lightweight principal, everyone else is reloaded from ``nlp_users`` by
    username (the signed session carries the username as ``user_id``).
    """
    if isinstance(claims, DatabaseSessionClaims):
        factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
        if factory is None:
            raise AuthenticationError("database authentication is unavailable")
        async with factory() as session:
            return await rbac_service.principal_for_user_id(session, claims.user_id)
    if claims.roles == frozenset({"guest"}):
        return claims.principal()
    factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
    if factory is None:
        return claims.principal()
    async with factory() as session:
        return await rbac_service.principal_for_username(session, claims.user_id)


async def get_write_access(
    request: Request,
    claims: Annotated[SessionClaims | DatabaseSessionClaims, Depends(_claims)],
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> SessionClaims:
    """Validate CSRF token and same-origin for state-changing requests."""
    if isinstance(claims, DatabaseSessionClaims):
        database_auth = request.app.state.database_auth
        database_auth.require_same_origin(request.headers.get("origin"), request.headers.get("host"))
        database_auth.require_csrf(claims, csrf_token)
    else:
        auth: SameOriginSessionAuth = request.app.state.auth
        auth.require_same_origin(request.headers.get("origin"), request.headers.get("host"))
        auth.require_csrf(claims, csrf_token)
    return claims


Principal = Annotated[AuthenticatedPrincipal, Depends(get_current_principal)]
WriteClaims = Annotated[SessionClaims | DatabaseSessionClaims, Depends(get_write_access)]
