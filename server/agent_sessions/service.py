"""Agent session service with workspace isolation.

Manages agent session lifecycle (create, list, update, delete).
Turn execution is handled by the existing gateway infrastructure
(server/web/app.py → BackendGateway).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.identity import AuthenticatedPrincipal

from .models import AgentSessionModel
from .schemas import AgentSessionCreate


class AgentSessionServiceError(Exception):
    """Base error for agent session operations."""


class AgentSessionNotFoundError(AgentSessionServiceError):
    """Raised when a session is not found."""


class WorkspaceAccessDeniedError(AgentSessionServiceError):
    """Raised when workspace access is denied."""


class AgentSessionService:
    """Service for agent session lifecycle with workspace isolation.

    All operations require an AuthenticatedPrincipal and enforce
    workspace scope to prevent cross-tenant data leakage.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _verify_workspace_access(
        self,
        principal: AuthenticatedPrincipal,
        workspace_id: str,
    ) -> None:
        """Verify principal has access to the workspace."""
        try:
            principal.require_workspace(workspace_id)
        except Exception as e:
            raise WorkspaceAccessDeniedError(
                f"Access denied to workspace {workspace_id}"
            ) from e

    async def create_session(
        self,
        principal: AuthenticatedPrincipal,
        data: AgentSessionCreate,
        *,
        workspace_id: str | None = None,
    ) -> AgentSessionModel:
        """Create a new agent session in the specified workspace."""
        # Resolve workspace
        if workspace_id is None:
            workspace_id = next(iter(principal.workspace_ids), None)
        if workspace_id is None:
            raise WorkspaceAccessDeniedError("No workspace available")

        self._verify_workspace_access(principal, workspace_id)

        session = AgentSessionModel(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            created_by_user_id=principal.user_id,
            title=data.title,
            status="active",
            metadata_json=data.metadata,
        )
        self.session.add(session)
        await self.session.flush()
        return session

    async def get_session(
        self,
        principal: AuthenticatedPrincipal,
        session_id: str,
    ) -> Optional[AgentSessionModel]:
        """Get a session by ID within the principal's workspace.

        Returns None if session doesn't exist OR doesn't belong to
        an accessible workspace. This prevents enumeration of other
        workspaces' sessions.
        """
        workspace_ids = principal.workspace_ids

        query = select(AgentSessionModel).where(
            AgentSessionModel.id == session_id,
            AgentSessionModel.status != "deleted",
        )

        # Non-admin users can only see their workspace's sessions
        if "*" not in workspace_ids:
            query = query.where(AgentSessionModel.workspace_id.in_(workspace_ids))

        return await self.session.scalar(query)

    async def list_sessions(
        self,
        principal: AuthenticatedPrincipal,
        *,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[AgentSessionModel], int]:
        """List sessions in the principal's accessible workspaces."""
        workspace_ids = principal.workspace_ids

        query = select(AgentSessionModel).where(
            AgentSessionModel.status != "deleted",
        )
        count_query = select(func.count()).select_from(AgentSessionModel).where(
            AgentSessionModel.status != "deleted",
        )

        if "*" not in workspace_ids:
            query = query.where(AgentSessionModel.workspace_id.in_(workspace_ids))
            count_query = count_query.where(AgentSessionModel.workspace_id.in_(workspace_ids))

        if status:
            query = query.where(AgentSessionModel.status == status)
            count_query = count_query.where(AgentSessionModel.status == status)

        query = query.order_by(AgentSessionModel.updated_at.desc()).offset(offset).limit(limit)

        sessions = list(await self.session.scalars(query))
        total = await self.session.scalar(count_query) or 0

        return sessions, total

    async def update_session(
        self,
        principal: AuthenticatedPrincipal,
        session_id: str,
        *,
        title: Optional[str] = None,
        status: Optional[str] = None,
    ) -> AgentSessionModel:
        """Update a session within the principal's workspace."""
        agent_session = await self.get_session(principal, session_id)
        if agent_session is None:
            raise AgentSessionNotFoundError(f"Session {session_id} not found")

        # Check ownership or admin
        if principal.user_id != agent_session.created_by_user_id and not principal.is_admin:
            raise WorkspaceAccessDeniedError("Cannot modify session owned by another user")

        if title is not None:
            agent_session.title = title
        if status is not None:
            agent_session.status = status

        agent_session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await self.session.flush()
        return agent_session

    async def delete_session(
        self,
        principal: AuthenticatedPrincipal,
        session_id: str,
    ) -> bool:
        """Soft delete a session within the principal's workspace."""
        agent_session = await self.get_session(principal, session_id)
        if agent_session is None:
            return False

        # Check ownership or admin
        if principal.user_id != agent_session.created_by_user_id and not principal.is_admin:
            raise WorkspaceAccessDeniedError("Cannot delete session owned by another user")

        agent_session.status = "deleted"
        agent_session.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await self.session.flush()
        return True
