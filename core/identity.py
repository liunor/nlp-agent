"""Authenticated identity contracts shared by Gateway-facing services."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from core.session_context import SessionContext


class AccessDeniedError(PermissionError):
    """Raised when an authenticated principal does not own a resource."""


class AuthenticatedPrincipal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str
    workspace_ids: frozenset[str] = Field(default_factory=lambda: frozenset({"default"}))
    # Active classroom memberships are projected into the runtime principal so
    # object-level classroom grants never have to infer membership from a workspace.
    classroom_ids: frozenset[str] = Field(default_factory=frozenset)
    roles: frozenset[str] = Field(default_factory=frozenset)
    # Persisted grant codes. Empty retains the compatibility role catalogue for
    # local/test identities that are intentionally not backed by MySQL.
    permissions: frozenset[str] = Field(default_factory=frozenset)
    permission_scopes: dict[str, frozenset[str]] = Field(default_factory=dict)
    # Incremented whenever role assignments change.  It is carried through
    # the request context so long-lived transports can detect an authz change.
    authorization_version: int = Field(default=0, ge=0)

    @classmethod
    def system_admin(cls) -> "AuthenticatedPrincipal":
        return cls(user_id="system", workspace_ids=frozenset({"*"}), roles=frozenset({"admin"}))

    @property
    def is_admin(self) -> bool:
        return bool({"admin", "developer"} & self.roles)

    def can_access(self, context: SessionContext) -> bool:
        return self.user_id == context.user_id and (
            "*" in self.workspace_ids or context.workspace_id in self.workspace_ids
        )

    def require_context(self, context: SessionContext) -> None:
        if not self.can_access(context):
            raise AccessDeniedError(
                f"principal {self.user_id!r} cannot access session {context.session_id!r}"
            )

    def require_workspace(self, workspace_id: str) -> None:
        if "*" not in self.workspace_ids and workspace_id not in self.workspace_ids:
            raise AccessDeniedError(
                f"principal {self.user_id!r} cannot access workspace {workspace_id!r}"
            )
