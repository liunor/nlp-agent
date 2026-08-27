"""Trusted sandbox request scope and lifecycle vocabulary.

The browser may select a visible workspace, but it must never select a sandbox
owner.  The owner is always the immutable database-authenticated user id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from core.identity import AuthenticatedPrincipal

if TYPE_CHECKING:
    from server.web.database_auth import DatabaseSessionClaims


class SandboxScopeError(PermissionError):
    """Raised when authenticated identity cannot safely scope a sandbox call."""


@dataclass(frozen=True)
class SandboxScope:
    """Server-derived identity used for every Sandbox Manager command.

    ``workspace_id`` is retained as audit context only.  It is intentionally
    excluded from ``environment_owner_key`` so members of a shared classroom
    never end up sharing a runtime.
    """

    owner_user_id: str
    auth_session_id: str
    workspace_id: str
    generation: int
    lease_expires_at: datetime

    @property
    def environment_owner_key(self) -> str:
        return self.owner_user_id

    @classmethod
    def from_authenticated_request(
        cls,
        principal: AuthenticatedPrincipal,
        claims: "DatabaseSessionClaims",
    ) -> "SandboxScope":
        if principal.user_id != claims.user_id:
            raise SandboxScopeError("sandbox user identity does not match database session")
        try:
            principal.require_workspace(claims.workspace_id)
        except PermissionError as error:
            raise SandboxScopeError("sandbox workspace is not available to this user") from error
        return cls(
            owner_user_id=claims.user_id,
            auth_session_id=claims.session_id,
            workspace_id=claims.workspace_id,
            generation=claims.authorization_version,
            lease_expires_at=claims.expires_at,
        )
