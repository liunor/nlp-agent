"""Turn application service for submitting and managing turns."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session

from application.identity.authorization import AccessDeniedError, WorkspacePrincipal
from crud.agent_session_crud import AgentSessionCRUD
from crud.turn_crud import TurnCRUD
from crud.workspace_crud import WorkspaceCRUD
from models.agent_state import Turn, TurnEvent


class TurnApplication:
    """Application service for turn operations.

    Handles turn submission, cancellation, and event replay with
    workspace isolation enforced at every step.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.turn_crud = TurnCRUD(session)
        self.agent_session_crud = AgentSessionCRUD(session)
        self.workspace_crud = WorkspaceCRUD(session)

    def _verify_workspace_access(
        self, principal: WorkspacePrincipal
    ) -> None:
        """Verify principal has access to their workspace."""
        if not self.workspace_crud.is_member(
            principal.workspace_id, principal.user_id
        ):
            raise AccessDeniedError(
                f"User {principal.user_id} is not a member of workspace {principal.workspace_id}"
            )

    def _verify_session_access(
        self,
        principal: WorkspacePrincipal,
        session_id: str,
    ) -> None:
        """Verify session belongs to principal's workspace."""
        agent_session = self.agent_session_crud.get_by_id_and_workspace(
            session_id, principal.workspace_id
        )
        if agent_session is None:
            raise FileNotFoundError(
                f"Session {session_id} not found in workspace {principal.workspace_id}"
            )

    def submit_turn(
        self,
        principal: WorkspacePrincipal,
        session_id: str,
        *,
        input_payload: dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> Turn:
        """Submit a new turn for execution.

        Uses idempotency key to prevent duplicate submissions.
        """
        self._verify_workspace_access(principal)
        self._verify_session_access(principal, session_id)

        if idempotency_key is None:
            idempotency_key = str(uuid.uuid4())

        existing = self.turn_crud.get_by_idempotency_key(
            workspace_id=principal.workspace_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return existing

        active = self.turn_crud.get_active_turn_for_session(session_id)
        if active is not None:
            raise ValueError(
                f"Session {session_id} already has an active turn: {active.id}"
            )

        turn = self.turn_crud.create(
            workspace_id=principal.workspace_id,
            session_id=session_id,
            submitted_by_user_id=principal.user_id,
            idempotency_key=idempotency_key,
            input_payload=input_payload,
        )

        self.agent_session_crud.update(
            session_id, active_turn_id=turn.id
        )

        return turn

    def get_turn(
        self,
        principal: WorkspacePrincipal,
        turn_id: str,
    ) -> Optional[Turn]:
        """Get a turn by ID within the principal's workspace."""
        self._verify_workspace_access(principal)
        return self.turn_crud.get_by_id_and_workspace(
            turn_id, principal.workspace_id
        )

    def cancel_turn(
        self,
        principal: WorkspacePrincipal,
        turn_id: str,
    ) -> Optional[Turn]:
        """Request cancellation of a turn."""
        self._verify_workspace_access(principal)

        turn = self.turn_crud.get_by_id_and_workspace(
            turn_id, principal.workspace_id
        )
        if turn is None:
            return None

        if turn.submitted_by_user_id != principal.user_id and not principal.is_admin:
            principal.require_role("owner")

        return self.turn_crud.request_cancel(turn_id)

    def list_turns(
        self,
        principal: WorkspacePrincipal,
        session_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Turn]:
        """List turns for a session within the principal's workspace."""
        self._verify_workspace_access(principal)
        self._verify_session_access(principal, session_id)
        return self.turn_crud.list_for_session(
            session_id, limit=limit, offset=offset
        )

    def add_event(
        self,
        principal: WorkspacePrincipal,
        turn_id: str,
        *,
        event_type: str,
        payload: dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> TurnEvent:
        """Add an event to a turn."""
        self._verify_workspace_access(principal)

        turn = self.turn_crud.get_by_id_and_workspace(
            turn_id, principal.workspace_id
        )
        if turn is None:
            raise FileNotFoundError(
                f"Turn {turn_id} not found in workspace {principal.workspace_id}"
            )

        latest_seq = self.turn_crud.get_latest_sequence(turn_id)
        sequence = latest_seq + 1

        return self.turn_crud.add_event(
            turn_id=turn_id,
            sequence=sequence,
            type=event_type,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    def get_events(
        self,
        principal: WorkspacePrincipal,
        turn_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> list[TurnEvent]:
        """Get events for a turn within the principal's workspace."""
        self._verify_workspace_access(principal)

        turn = self.turn_crud.get_by_id_and_workspace(
            turn_id, principal.workspace_id
        )
        if turn is None:
            raise FileNotFoundError(
                f"Turn {turn_id} not found in workspace {principal.workspace_id}"
            )

        return self.turn_crud.get_events(
            turn_id, after_sequence=after_sequence, limit=limit
        )

    def complete_turn(
        self,
        principal: WorkspacePrincipal,
        turn_id: str,
        *,
        output_summary: Optional[dict[str, Any]] = None,
    ) -> Optional[Turn]:
        """Mark a turn as completed."""
        self._verify_workspace_access(principal)

        turn = self.turn_crud.get_by_id_and_workspace(
            turn_id, principal.workspace_id
        )
        if turn is None:
            return None

        result = self.turn_crud.update_state(
            turn_id, "completed", output_summary=output_summary
        )

        if result is not None:
            self.agent_session_crud.clear_active_turn(turn.session_id)

        return result

    def fail_turn(
        self,
        principal: WorkspacePrincipal,
        turn_id: str,
        *,
        output_summary: Optional[dict[str, Any]] = None,
    ) -> Optional[Turn]:
        """Mark a turn as failed."""
        self._verify_workspace_access(principal)

        turn = self.turn_crud.get_by_id_and_workspace(
            turn_id, principal.workspace_id
        )
        if turn is None:
            return None

        result = self.turn_crud.update_state(
            turn_id, "failed", output_summary=output_summary
        )

        if result is not None:
            self.agent_session_crud.clear_active_turn(turn.session_id)

        return result
