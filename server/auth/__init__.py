"""Authentication module.

Provides FastAPI dependencies for authentication and authorization.
The auth dependencies bridge with the existing SameOriginSessionAuth
and resolve full RBAC principals from MySQL.
"""

from server.auth.dependencies import (
    Principal,
    WriteClaims,
    get_current_principal,
    get_db_session,
    get_write_access,
)
from server.auth.schemas import (
    LoginRequest,
    LoginResponse,
    MeResponse,
    PasswordChangeRequest,
)


def roles_with_admin_alias(roles) -> set[str]:
    """RBAC treats ``admin`` and ``developer`` as equivalent.  Return a set
    containing both so consumers (frontend DeveloperWorkspace guard, etc.)
    that check ``roles.includes("admin")`` work for developer accounts.

    Used by /api/v1/auth/login, /api/v1/auth/session, and /api/v1/auth/me
    so all three identity responses agree on the alias.
    """
    s = set(roles)
    if "developer" in s or "admin" in s:
        s.add("admin")
        s.add("developer")
    return s


__all__ = [
    "Principal",
    "WriteClaims",
    "get_current_principal",
    "get_db_session",
    "get_write_access",
    "LoginRequest",
    "LoginResponse",
    "MeResponse",
    "PasswordChangeRequest",
    "roles_with_admin_alias",
]
