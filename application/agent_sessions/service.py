"""Agent session service with workspace isolation."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from application.identity.authorization import AccessDeniedError, WorkspacePrincipal
from crud.agent_session_crud import AgentSessionCRUD
from crud.workspace_crud import WorkspaceCRUD
from models.agent_state import AgentSession


class AgentSessionService:
    """Service for agent session operations with workspace isolation.

    All operations require a WorkspacePrincipal and enforce workspace scope.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.agent_session_crud = AgentSessionCRUD(session)
        self.workspace_crud = WorkspaceCRUD(session)

    def _verify_workspace_access(
        self, principal: WorkspacePrincipal
    ) -> None:
        """Verify principal has access to their workspace.
        
        Admins bypass membership check.
        """
        # Admins have access to all workspaces
        if principal.is_admin:
            return
            
        if not self.workspace_crud.is_member(
            principal.workspace_id, principal.user_id
        ):
            raise AccessDeniedError(
                f"User {principal.user_id} is not a member of workspace {principal.workspace_id}"
            )

    def create_session(
        self,
        principal: WorkspacePrincipal,
        *,
        title: str,
        model_profile_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> AgentSession:
        """Create a new agent session in the principal's workspace."""
        self._verify_workspace_access(principal)

        return self.agent_session_crud.create(
            workspace_id=principal.workspace_id,
            created_by_user_id=principal.user_id,
            title=title,
            model_profile_id=model_profile_id,
            metadata=metadata,
        )

    def get_session(
        self,
        principal: WorkspacePrincipal,
        session_id: str,
    ) -> Optional[AgentSession]:
        """Get a session by ID within the principal's workspace.

        Returns None if session doesn't exist OR doesn't belong to workspace.
        This prevents enumeration of other workspaces' sessions.
        """
        self._verify_workspace_access(principal)
        return self.agent_session_crud.get_by_id_and_workspace(
            session_id, principal.workspace_id
        )

    def _require_session(
        self,
        principal: WorkspacePrincipal,
        session_id: str,
    ) -> AgentSession:
        """Get session or raise not found."""
        agent_session = self.get_session(principal, session_id)
        if agent_session is None:
            raise FileNotFoundError(
                f"Session {session_id} not found in workspace {principal.workspace_id}"
            )
        return agent_session

    def list_sessions(
        self,
        principal: WorkspacePrincipal,
        *,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentSession]:
        """List sessions in the principal's workspace."""
        self._verify_workspace_access(principal)
        return self.agent_session_crud.list_for_workspace(
            principal.workspace_id,
            status=status,
            limit=limit,
            offset=offset,
        )

    def update_session(
        self,
        principal: WorkspacePrincipal,
        session_id: str,
        *,
        title: Optional[str] = None,
        status: Optional[str] = None,
    ) -> AgentSession:
        """Update a session within the principal's workspace."""
        agent_session = self._require_session(principal, session_id)

        if principal.user_id != agent_session.created_by_user_id and not principal.is_admin:
            principal.require_role("owner", "member")

        result = self.agent_session_crud.update(
            session_id, title=title, status=status
        )
        if result is None:
            raise FileNotFoundError(f"Session {session_id} not found")
        return result

    def delete_session(
        self,
        principal: WorkspacePrincipal,
        session_id: str,
    ) -> bool:
        """Soft delete a session within the principal's workspace."""
        agent_session = self._require_session(principal, session_id)

        if principal.user_id != agent_session.created_by_user_id and not principal.is_admin:
            principal.require_workspace_owner()

        return self.agent_session_crud.soft_delete(session_id)

    def set_active_turn(
        self,
        principal: WorkspacePrincipal,
        session_id: str,
        turn_id: str,
    ) -> None:
        """Set the active turn for a session."""
        self._require_session(principal, session_id)
        self.agent_session_crud.update(
            session_id, active_turn_id=turn_id
        )

    def clear_active_turn(
        self,
        principal: WorkspacePrincipal,
        session_id: str,
    ) -> None:
        """Clear the active turn for a session."""
        self._require_session(principal, session_id)
        self.agent_session_crud.clear_active_turn(session_id)
