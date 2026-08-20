"""Workspace management service.

Integrates with existing RBAC infrastructure for workspace membership
and uses the shared MySQL async session factory.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.infrastructure.mysql.models import (
    OutboxMessageModel,
    WorkspaceModel,
    WorkspaceMemberModel,
    UserModel,
)

from .schemas import WorkspaceCreate


class WorkspaceServiceError(Exception):
    """Base error for workspace service operations."""


class WorkspaceNotFoundError(WorkspaceServiceError):
    """Raised when a workspace is not found."""


class WorkspaceAlreadyExistsError(WorkspaceServiceError):
    """Raised when attempting to create a duplicate workspace."""


class WorkspaceAccessDeniedError(WorkspaceServiceError):
    """Raised when access to a workspace is denied."""


class WorkspaceService:
    """Service for workspace management operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_workspace(
        self,
        data: WorkspaceCreate,
        *,
        created_by_user_id: str,
    ) -> WorkspaceModel:
        """Create a new workspace."""
        # Generate slug if not provided
        slug = data.slug or f"workspace-{uuid.uuid4().hex[:8]}"

        # Check for existing workspace with same slug
        existing = await self.session.scalar(
            select(WorkspaceModel.id).where(WorkspaceModel.slug == slug)
        )
        if existing:
            raise WorkspaceAlreadyExistsError(
                f"Workspace with slug '{slug}' already exists"
            )

        # Create workspace
        workspace = WorkspaceModel(
            id=str(uuid.uuid4()),
            slug=slug,
            name=data.name,
            status="active",
        )
        self.session.add(workspace)
        await self.session.flush()

        # Add creator as owner
        member = WorkspaceMemberModel(
            workspace_id=workspace.id,
            user_id=created_by_user_id,
            member_type="owner",
            status="active",
        )
        self.session.add(member)

        await self.session.flush()
        return workspace

    async def get_workspace(self, workspace_id: str) -> WorkspaceModel:
        """Get workspace by ID."""
        workspace = await self.session.scalar(
            select(WorkspaceModel).where(WorkspaceModel.id == workspace_id)
        )
        if workspace is None:
            raise WorkspaceNotFoundError(f"Workspace {workspace_id} not found")
        return workspace

    async def list_user_workspaces(
        self,
        user_id: str,
    ) -> list[WorkspaceModel]:
        """List all workspaces for a user."""
        query = (
            select(WorkspaceModel)
            .join(
                WorkspaceMemberModel,
                WorkspaceMemberModel.workspace_id == WorkspaceModel.id,
            )
            .where(
                WorkspaceMemberModel.user_id == user_id,
                WorkspaceMemberModel.status == "active",
                WorkspaceModel.status == "active",
            )
            .order_by(WorkspaceModel.name)
        )
        return list(await self.session.scalars(query))

    async def is_member(
        self,
        workspace_id: str,
        user_id: str,
    ) -> bool:
        """Check if a user is a member of a workspace."""
        member = await self.session.scalar(
            select(WorkspaceMemberModel.user_id).where(
                WorkspaceMemberModel.workspace_id == workspace_id,
                WorkspaceMemberModel.user_id == user_id,
                WorkspaceMemberModel.status == "active",
            )
        )
        return member is not None

    async def get_member_role(
        self,
        workspace_id: str,
        user_id: str,
    ) -> Optional[str]:
        """Get the member's role in a workspace."""
        member = await self.session.scalar(
            select(WorkspaceMemberModel.member_type).where(
                WorkspaceMemberModel.workspace_id == workspace_id,
                WorkspaceMemberModel.user_id == user_id,
                WorkspaceMemberModel.status == "active",
            )
        )
        return member

    async def add_member(
        self,
        workspace_id: str,
        user_id: str,
        *,
        member_type: str = "member",
        actor_user_id: str | None = None,
    ) -> WorkspaceMemberModel:
        """Add a member to a workspace."""
        workspace = await self.session.scalar(
            select(WorkspaceModel).where(
                WorkspaceModel.id == workspace_id,
                WorkspaceModel.status == "active",
            ).with_for_update()
        )
        if workspace is None:
            raise WorkspaceNotFoundError(f"Workspace {workspace_id} not found")
        user = await self.session.scalar(
            select(UserModel).where(
                UserModel.id == user_id,
                UserModel.status == "active",
                UserModel.deleted_at.is_(None),
            ).with_for_update()
        )
        if user is None:
            raise WorkspaceServiceError("User is not active")

        # Check if already a member
        existing = await self.session.scalar(
            select(WorkspaceMemberModel).where(
                WorkspaceMemberModel.workspace_id == workspace_id,
                WorkspaceMemberModel.user_id == user_id,
            )
        )
        if existing:
            if existing.status == "active":
                raise WorkspaceServiceError("User is already a member")
            # Reactivate membership
            existing.status = "active"
            existing.member_type = member_type
            user.authorization_version += 1
            self.session.add(OutboxMessageModel(id=str(uuid.uuid4()), topic="authorization.changed", payload_json={"user_id": user_id, "reason": "workspace_membership_changed"}))
            await self.session.flush()
            return existing

        # Add member
        member = WorkspaceMemberModel(
            workspace_id=workspace_id,
            user_id=user_id,
            member_type=member_type,
            status="active",
        )
        self.session.add(member)
        user.authorization_version += 1
        self.session.add(OutboxMessageModel(id=str(uuid.uuid4()), topic="authorization.changed", payload_json={"user_id": user_id, "reason": "workspace_membership_changed"}))
        await self.session.flush()
        return member

    async def remove_member(
        self,
        workspace_id: str,
        user_id: str,
        *,
        actor_user_id: str | None = None,
    ) -> bool:
        """Remove a member from a workspace."""
        member = await self.session.scalar(
            select(WorkspaceMemberModel).where(
                WorkspaceMemberModel.workspace_id == workspace_id,
                WorkspaceMemberModel.user_id == user_id,
            ).with_for_update()
        )
        if member is None:
            return False

        if member.member_type == "owner":
            owner_count = await self.session.scalar(
                select(func.count()).select_from(WorkspaceMemberModel).where(
                    WorkspaceMemberModel.workspace_id == workspace_id,
                    WorkspaceMemberModel.member_type == "owner",
                    WorkspaceMemberModel.status == "active",
                )
            )
            if int(owner_count or 0) <= 1:
                raise WorkspaceServiceError("cannot remove the last workspace owner")
        member.status = "removed"
        user = await self.session.scalar(select(UserModel).where(UserModel.id == user_id).with_for_update())
        if user is not None:
            user.authorization_version += 1
            self.session.add(OutboxMessageModel(id=str(uuid.uuid4()), topic="authorization.changed", payload_json={"user_id": user_id, "reason": "workspace_membership_changed"}))
        await self.session.flush()
        return True

    async def list_members(
        self,
        workspace_id: str,
    ) -> list[WorkspaceMemberModel]:
        """List all members of a workspace."""
        query = select(WorkspaceMemberModel).where(
            WorkspaceMemberModel.workspace_id == workspace_id,
            WorkspaceMemberModel.status == "active",
        )
        return list(await self.session.scalars(query))
