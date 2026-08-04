"""Workspace management service."""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from crud.role_crud import RoleCRUD
from crud.workspace_crud import WorkspaceCRUD
from models.agent_state import Workspace, WorkspaceMember


class WorkspaceService:
    """Service for workspace management operations."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.workspace_crud = WorkspaceCRUD(session)
        self.role_crud = RoleCRUD(session)

    def create_workspace(
        self,
        *,
        name: str,
        type: str,
        created_by_user_id: str,
    ) -> Workspace:
        """Create a new workspace and add creator as owner."""
        owner_role = self.role_crud.get_by_code("owner")
        if owner_role is None:
            roles = self.role_crud.ensure_default_roles()
            owner_role = roles["owner"]

        workspace = self.workspace_crud.create(
            name=name,
            type=type,
            created_by_user_id=created_by_user_id,
        )

        self.workspace_crud.add_member(
            workspace_id=workspace.id,
            user_id=created_by_user_id,
            role_id=owner_role.id,
        )

        return workspace

    def create_personal_workspace(
        self, user_id: str, name: Optional[str] = None
    ) -> Workspace:
        """Create a personal workspace for a user."""
        workspace_name = name or f"{user_id}'s workspace"
        return self.create_workspace(
            name=workspace_name,
            type="personal",
            created_by_user_id=user_id,
        )

    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
        """Get workspace by ID."""
        return self.workspace_crud.get_by_id(workspace_id)

    def list_user_workspaces(self, user_id: str) -> list[Workspace]:
        """List workspaces for a user."""
        return self.workspace_crud.list_workspaces_for_user(user_id)

    def update_workspace(
        self,
        workspace_id: str,
        *,
        name: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[Workspace]:
        """Update workspace."""
        return self.workspace_crud.update(
            workspace_id, name=name, status=status
        )

    def delete_workspace(self, workspace_id: str) -> bool:
        """Soft delete a workspace."""
        return self.workspace_crud.soft_delete(workspace_id)

    def add_member(
        self,
        workspace_id: str,
        user_id: str,
        role_code: str,
    ) -> Optional[WorkspaceMember]:
        """Add a member to workspace."""
        role = self.role_crud.get_by_code(role_code)
        if role is None:
            return None

        if not self.workspace_crud.is_member(workspace_id, user_id):
            return self.workspace_crud.add_member(
                workspace_id=workspace_id,
                user_id=user_id,
                role_id=role.id,
            )
        return None

    def remove_member(self, workspace_id: str, user_id: str) -> bool:
        """Remove a member from workspace."""
        return self.workspace_crud.remove_member(workspace_id, user_id)

    def list_members(self, workspace_id: str) -> list[WorkspaceMember]:
        """List workspace members."""
        return self.workspace_crud.list_members(workspace_id)

    def is_member(self, workspace_id: str, user_id: str) -> bool:
        """Check if user is a member of workspace."""
        return self.workspace_crud.is_member(workspace_id, user_id)

    def get_user_role_in_workspace(
        self, workspace_id: str, user_id: str
    ) -> Optional[str]:
        """Get user's role code in workspace."""
        return self.workspace_crud.get_user_workspace_role(workspace_id, user_id)
