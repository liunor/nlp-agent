"""Authorization contracts for workspace-scoped access control."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


class AccessDeniedError(PermissionError):
    """Raised when access to a resource is denied."""

    pass


@dataclass(frozen=True)
class WorkspacePrincipal:
    """Identity context for a request within a workspace.

    This is the authoritative identity for all operations.
    It combines user identity with workspace scope.
    """

    user_id: str
    auth_session_id: str
    workspace_id: str
    system_roles: frozenset[str] = field(default_factory=frozenset)
    workspace_role: Optional[str] = None

    @property
    def is_admin(self) -> bool:
        """Check if user has system admin role."""
        return "admin" in self.system_roles

    @property
    def is_developer(self) -> bool:
        """Check if user has developer role."""
        return "developer" in self.system_roles

    @property
    def is_operations(self) -> bool:
        """Check if user has operations role."""
        return "operations" in self.system_roles

    @property
    def is_teacher(self) -> bool:
        """Check if user has teacher role."""
        return "teacher" in self.system_roles

    @property
    def is_workspace_owner(self) -> bool:
        """Check if user is owner of current workspace."""
        return self.workspace_role == "owner"

    def require_role(self, *roles: str) -> None:
        """Require user to have one of the specified roles."""
        if self.is_admin:
            return
        if self.workspace_role in roles:
            return
        if self.system_roles & frozenset(roles):
            return
        raise AccessDeniedError(
            f"User {self.user_id} does not have required role in workspace {self.workspace_id}"
        )

    def require_admin(self) -> None:
        """Require system admin role."""
        if not self.is_admin:
            raise AccessDeniedError(
                f"System admin role required for user {self.user_id}"
            )

    def require_workspace_owner(self) -> None:
        """Require workspace owner role."""
        if self.is_admin:
            return
        if self.workspace_role != "owner":
            raise AccessDeniedError(
                f"Workspace owner role required for user {self.user_id}"
            )


def require_workspace_access(
    principal: WorkspacePrincipal,
    workspace_id: str,
) -> None:
    """Verify that principal has access to the specified workspace.

    This should be called for every request that accesses workspace data.
    """
    if principal.workspace_id != workspace_id:
        raise AccessDeniedError(
            f"Principal workspace {principal.workspace_id} does not match "
            f"requested workspace {workspace_id}"
        )
