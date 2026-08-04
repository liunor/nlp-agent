"""Agent session CRUD operations."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from db.database import generate_uuid7, utc_now
from models.agent_state import AgentSession


class AgentSessionCRUD:
    """CRUD operations for AgentSession model."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        workspace_id: str,
        created_by_user_id: str,
        title: str,
        model_profile_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> AgentSession:
        """Create a new agent session."""
        agent_session = AgentSession(
            id=generate_uuid7(),
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            title=title,
            status="active",
            model_profile_id=model_profile_id,
            metadata_=metadata or {},
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.session.add(agent_session)
        self.session.flush()
        return agent_session

    def get_by_id(self, session_id: str) -> Optional[AgentSession]:
        """Get agent session by ID."""
        stmt = select(AgentSession).where(
            AgentSession.id == session_id, AgentSession.deleted_at.is_(None)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def get_by_id_and_workspace(
        self, session_id: str, workspace_id: str
    ) -> Optional[AgentSession]:
        """Get agent session by ID and workspace (isolation check)."""
        stmt = select(AgentSession).where(
            AgentSession.id == session_id,
            AgentSession.workspace_id == workspace_id,
            AgentSession.deleted_at.is_(None),
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_for_workspace(
        self,
        workspace_id: str,
        *,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentSession]:
        """List agent sessions for a workspace."""
        stmt = (
            select(AgentSession)
            .where(
                AgentSession.workspace_id == workspace_id,
                AgentSession.deleted_at.is_(None),
            )
            .order_by(AgentSession.updated_at.desc())
        )
        if status:
            stmt = stmt.where(AgentSession.status == status)
        stmt = stmt.offset(offset).limit(limit)
        return list(self.session.execute(stmt).scalars().all())

    def update(
        self,
        session_id: str,
        *,
        title: Optional[str] = None,
        status: Optional[str] = None,
        active_turn_id: Optional[str] = None,
    ) -> Optional[AgentSession]:
        """Update agent session."""
        agent_session = self.get_by_id(session_id)
        if agent_session is None:
            return None

        if title is not None:
            agent_session.title = title
        if status is not None:
            agent_session.status = status
        if active_turn_id is not None:
            agent_session.active_turn_id = active_turn_id
        agent_session.updated_at = utc_now()

        self.session.flush()
        return agent_session

    def clear_active_turn(self, session_id: str) -> bool:
        """Clear the active turn ID."""
        stmt = (
            update(AgentSession)
            .where(AgentSession.id == session_id)
            .values(active_turn_id=None, updated_at=utc_now())
        )
        self.session.execute(stmt)
        return True

    def soft_delete(self, session_id: str) -> bool:
        """Soft delete an agent session."""
        agent_session = self.get_by_id(session_id)
        if agent_session is None:
            return False

        agent_session.status = "deleted"
        agent_session.deleted_at = utc_now()
        agent_session.updated_at = utc_now()
        self.session.flush()
        return True
