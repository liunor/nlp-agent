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
]
