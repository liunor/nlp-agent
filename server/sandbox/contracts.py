"""Trusted sandbox request scope and lifecycle vocabulary.

The browser may select a visible workspace, but it must never select a sandbox
owner.  The owner is always the immutable database-authenticated user id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import TYPE_CHECKING

from core.identity import AuthenticatedPrincipal

if TYPE_CHECKING:
    from server.web.database_auth import DatabaseSessionClaims


class SandboxScopeError(PermissionError):
    """Raised when authenticated identity cannot safely scope a sandbox call."""


class SandboxCapacityError(RuntimeError):
    messages = {
        "sandbox_host_runtime_limit": "The sandbox host runtime limit has been reached.",
        "sandbox_host_memory_reserve": "Available host memory is below the amount needed for another sandbox.",
        "sandbox_host_disk_reserve": "Available host disk space is below the sandbox reserve.",
    }

    def __init__(self, code: str, *, actual: float | None, threshold: float) -> None:
        super().__init__(self.messages[code])
        self.code = code
        self.actual = actual if type(actual) in (int, float) and math.isfinite(actual) else None
        if type(threshold) not in (int, float) or not math.isfinite(threshold):
            raise ValueError("sandbox capacity threshold must be numeric")
        self.threshold = threshold


class SandboxScratchTimeoutError(TimeoutError):
    """The isolated scratch container exceeded its execution deadline."""

    code = "scratch_execution_timeout"
    message = "Sandbox scratch execution timed out."

    def __init__(self) -> None:
        super().__init__(self.message)


@dataclass(frozen=True)
class SandboxScope:
    """Server-derived identity used for every Sandbox Manager command.

    Environments are scoped by both user and workspace, so neither classmates
    nor one user's different workspaces share a runtime or mounted file tree.
    """

    owner_user_id: str
    auth_session_id: str
    workspace_id: str
    generation: int
    lease_expires_at: datetime

    @property
    def environment_owner_key(self) -> str:
        return f"{self.owner_user_id}:{self.workspace_id}"

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
