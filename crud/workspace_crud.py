"""Workspace CRUD operations."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from db.database import generate_uuid7, utc_now
from models.agent_state import Workspace, WorkspaceMember
from models.identity import Role


class WorkspaceCRUD:
    """CRUD operations for Workspace model."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        name: str,
        type: str,
        created_by_user_id: str,
    ) -> Workspace:
        """Create a new workspace."""
        workspace = Workspace(
            id=generate_uuid7(),
            name=name,
            type=type,
            status="active",
            created_by_user_id=created_by_user_id,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.session.add(workspace)
        self.session.flush()
        return workspace

    def get_by_id(self, workspace_id: str) -> Optional[Workspace]:
        """Get workspace by ID."""
        stmt = select(Workspace).where(
            Workspace.id == workspace_id, Workspace.deleted_at.is_(None)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_workspaces_for_user(
        self,
        user_id: str,
        *,
        status: Optional[str] = None,
    ) -> list[Workspace]:
        """List workspaces where user is an active member."""
        stmt = (
            select(Workspace)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.status == "active",
                Workspace.deleted_at.is_(None),
            )
        )
        if status:
            stmt = stmt.where(Workspace.status == status)
        return list(self.session.execute(stmt).scalars().all())

    def update(
        self,
        workspace_id: str,
        *,
        name: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[Workspace]:
        """Update workspace."""
        workspace = self.get_by_id(workspace_id)
        if workspace is None:
            return None

        if name is not None:
            workspace.name = name
        if status is not None:
            workspace.status = status
        workspace.updated_at = utc_now()

        self.session.flush()
        return workspace

    def soft_delete(self, workspace_id: str) -> bool:
        """Soft delete a workspace."""
        workspace = self.get_by_id(workspace_id)
        if workspace is None:
            return False

        workspace.status = "deleted"
        workspace.deleted_at = utc_now()
        workspace.updated_at = utc_now()
        self.session.flush()
        return True

    def add_member(
        self,
        workspace_id: str,
        user_id: str,
        role_id: str,
    ) -> WorkspaceMember:
        """Add a member to workspace."""
        member = WorkspaceMember(
            workspace_id=workspace_id,
            user_id=user_id,
            role_id=role_id,
            status="active",
            joined_at=utc_now(),
        )
        self.session.add(member)
        self.session.flush()
        return member

    def remove_member(self, workspace_id: str, user_id: str) -> bool:
        """Remove a member from workspace."""
        stmt = select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
        member = self.session.execute(stmt).scalar_one_or_none()
        if member is None:
            return False

        member.status = "removed"
        self.session.flush()
        return True

    def get_member(
        self, workspace_id: str, user_id: str
    ) -> Optional[WorkspaceMember]:
        """Get workspace member."""
        stmt = select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
            WorkspaceMember.status == "active",
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_members(self, workspace_id: str) -> list[WorkspaceMember]:
        """List active members of a workspace."""
        stmt = (
            select(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.status == "active",
            )
            .order_by(WorkspaceMember.joined_at)
        )
        return list(self.session.execute(stmt).scalars().all())

    def is_member(self, workspace_id: str, user_id: str) -> bool:
        """Check if user is an active member of workspace."""
        return self.get_member(workspace_id, user_id) is not None

    def get_user_workspace_role(
        self, workspace_id: str, user_id: str
    ) -> Optional[str]:
        """Get user's role code in workspace."""
        stmt = (
            select(Role.code)
            .join(WorkspaceMember, WorkspaceMember.role_id == Role.id)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.status == "active",
            )
        )
        return self.session.execute(stmt).scalar_one_or_none()
