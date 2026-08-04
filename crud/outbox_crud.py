"""Outbox message CRUD operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.database import generate_uuid7, utc_now
from models.agent_state import OutboxMessage


class OutboxCRUD:
    """CRUD operations for OutboxMessage model."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        workspace_id: str,
        kind: str,
        aggregate_id: str,
        payload: dict[str, Any],
    ) -> OutboxMessage:
        """Create a new outbox message."""
        message = OutboxMessage(
            id=generate_uuid7(),
            workspace_id=workspace_id,
            kind=kind,
            aggregate_id=aggregate_id,
            payload=payload,
            occurred_at=utc_now(),
        )
        self.session.add(message)
        self.session.flush()
        return message

    def get_unpublished(
        self, *, limit: int = 100
    ) -> list[OutboxMessage]:
        """Get unpublished messages."""
        stmt = (
            select(OutboxMessage)
            .where(OutboxMessage.published_at.is_(None))
            .order_by(OutboxMessage.occurred_at)
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars().all())

    def mark_published(
        self,
        message_id: str,
    ) -> bool:
        """Mark a message as published."""
        message = self.get_by_id(message_id)
        if message is None:
            return False

        message.published_at = utc_now()
        self.session.flush()
        return True

    def increment_attempts(self, message_id: str) -> int:
        """Increment the attempt counter."""
        message = self.get_by_id(message_id)
        if message is None:
            return 0

        message.attempts += 1
        self.session.flush()
        return message.attempts

    def get_by_id(self, message_id: str) -> Optional[OutboxMessage]:
        """Get message by ID."""
        stmt = select(OutboxMessage).where(OutboxMessage.id == message_id)
        return self.session.execute(stmt).scalar_one_or_none()

    def delete_published_before(
        self, before: datetime
    ) -> int:
        """Delete published messages before a timestamp."""
        stmt = select(OutboxMessage).where(
            OutboxMessage.published_at.isnot(None),
            OutboxMessage.published_at < before,
        )
        messages = self.session.execute(stmt).scalars().all()
        count = 0
        for message in messages:
            self.session.delete(message)
            count += 1
        self.session.flush()
        return count
