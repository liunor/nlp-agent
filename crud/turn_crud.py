"""Turn CRUD operations."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select, update, func
from sqlalchemy.orm import Session

from db.database import generate_uuid7, utc_now
from models.agent_state import Turn, TurnEvent


class TurnCRUD:
    """CRUD operations for Turn model."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        workspace_id: str,
        session_id: str,
        submitted_by_user_id: str,
        idempotency_key: str,
        input_payload: dict[str, Any],
    ) -> Turn:
        """Create a new turn."""
        turn = Turn(
            id=generate_uuid7(),
            workspace_id=workspace_id,
            session_id=session_id,
            submitted_by_user_id=submitted_by_user_id,
            idempotency_key=idempotency_key,
            state="accepted",
            input_payload=input_payload,
            version=1,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.session.add(turn)
        self.session.flush()
        return turn

    def get_by_id(self, turn_id: str) -> Optional[Turn]:
        """Get turn by ID."""
        stmt = select(Turn).where(Turn.id == turn_id)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_by_id_and_workspace(
        self, turn_id: str, workspace_id: str
    ) -> Optional[Turn]:
        """Get turn by ID and workspace (isolation check)."""
        stmt = select(Turn).where(
            Turn.id == turn_id, Turn.workspace_id == workspace_id
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def get_by_idempotency_key(
        self,
        *,
        workspace_id: str,
        session_id: str,
        idempotency_key: str,
    ) -> Optional[Turn]:
        """Get turn by idempotency key."""
        stmt = select(Turn).where(
            Turn.workspace_id == workspace_id,
            Turn.session_id == session_id,
            Turn.idempotency_key == idempotency_key,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def get_active_turn_for_session(
        self, session_id: str
    ) -> Optional[Turn]:
        """Get active turn for a session."""
        stmt = select(Turn).where(
            Turn.session_id == session_id,
            Turn.state.in_(["accepted", "queued", "running"]),
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_for_session(
        self,
        session_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Turn]:
        """List turns for a session."""
        stmt = (
            select(Turn)
            .where(Turn.session_id == session_id)
            .order_by(Turn.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars().all())

    def update_state(
        self,
        turn_id: str,
        state: str,
        *,
        output_summary: Optional[dict[str, Any]] = None,
    ) -> Optional[Turn]:
        """Update turn state."""
        turn = self.get_by_id(turn_id)
        if turn is None:
            return None

        turn.state = state
        turn.updated_at = utc_now()

        if state == "running" and turn.started_at is None:
            turn.started_at = utc_now()
        if state in ("completed", "cancelled", "failed"):
            turn.completed_at = utc_now()
        if output_summary is not None:
            turn.output_summary = output_summary

        self.session.flush()
        return turn

    def request_cancel(self, turn_id: str) -> Optional[Turn]:
        """Request turn cancellation."""
        turn = self.get_by_id(turn_id)
        if turn is None:
            return None
        if turn.state not in ("accepted", "queued", "running"):
            return turn

        turn.state = "cancelling"
        turn.cancel_requested_at = utc_now()
        turn.updated_at = utc_now()
        self.session.flush()
        return turn

    def increment_version(self, turn_id: str) -> int:
        """Increment turn version for optimistic locking."""
        turn = self.get_by_id(turn_id)
        if turn is None:
            return 0
        turn.version += 1
        turn.updated_at = utc_now()
        self.session.flush()
        return turn.version

    def add_event(
        self,
        *,
        turn_id: str,
        sequence: int,
        type: str,
        payload: dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> TurnEvent:
        """Add an event to a turn."""
        event = TurnEvent(
            id=generate_uuid7(),
            turn_id=turn_id,
            sequence=sequence,
            type=type,
            idempotency_key=idempotency_key,
            payload=payload,
            created_at=utc_now(),
        )
        self.session.add(event)
        self.session.flush()
        return event

    def get_events(
        self,
        turn_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> list[TurnEvent]:
        """Get events for a turn."""
        stmt = (
            select(TurnEvent)
            .where(
                TurnEvent.turn_id == turn_id,
                TurnEvent.sequence > after_sequence,
            )
            .order_by(TurnEvent.sequence)
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_latest_sequence(self, turn_id: str) -> int:
        """Get the latest event sequence number for a turn."""
        stmt = (
            select(func.max(TurnEvent.sequence))
            .where(TurnEvent.turn_id == turn_id)
        )
        result = self.session.execute(stmt).scalar()
        return result or 0
