"""Checkpoint CRUD operations."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.database import generate_uuid7, utc_now
from models.agent_state import AgentCheckpoint


class CheckpointCRUD:
    """CRUD operations for AgentCheckpoint model."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        workspace_id: str,
        session_id: str,
        turn_id: Optional[str],
        checkpoint_no: int,
        state_version: str,
        state_blob: bytes,
        state_sha256: bytes,
        encrypted_data_key: Optional[bytes] = None,
        expires_at: Optional[datetime] = None,
    ) -> AgentCheckpoint:
        """Create a new checkpoint."""
        checkpoint = AgentCheckpoint(
            id=generate_uuid7(),
            workspace_id=workspace_id,
            session_id=session_id,
            turn_id=turn_id,
            checkpoint_no=checkpoint_no,
            state_version=state_version,
            state_blob=state_blob,
            state_sha256=state_sha256,
            encrypted_data_key=encrypted_data_key,
            status="ready",
            created_at=utc_now(),
            expires_at=expires_at,
        )
        self.session.add(checkpoint)
        self.session.flush()
        return checkpoint

    def get_by_id(self, checkpoint_id: str) -> Optional[AgentCheckpoint]:
        """Get checkpoint by ID."""
        stmt = select(AgentCheckpoint).where(AgentCheckpoint.id == checkpoint_id)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_by_session_and_number(
        self, session_id: str, checkpoint_no: int
    ) -> Optional[AgentCheckpoint]:
        """Get checkpoint by session and checkpoint number."""
        stmt = select(AgentCheckpoint).where(
            AgentCheckpoint.session_id == session_id,
            AgentCheckpoint.checkpoint_no == checkpoint_no,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def get_latest_for_session(
        self, session_id: str
    ) -> Optional[AgentCheckpoint]:
        """Get the latest checkpoint for a session."""
        stmt = (
            select(AgentCheckpoint)
            .where(
                AgentCheckpoint.session_id == session_id,
                AgentCheckpoint.status == "ready",
            )
            .order_by(AgentCheckpoint.checkpoint_no.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def get_for_workspace_and_session(
        self, workspace_id: str, session_id: str
    ) -> list[AgentCheckpoint]:
        """Get checkpoints for a workspace and session (isolation check)."""
        stmt = (
            select(AgentCheckpoint)
            .where(
                AgentCheckpoint.workspace_id == workspace_id,
                AgentCheckpoint.session_id == session_id,
            )
            .order_by(AgentCheckpoint.checkpoint_no.desc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def supersede_previous(
        self, session_id: str, checkpoint_no: int
    ) -> int:
        """Mark all previous checkpoints as superseded."""
        stmt = select(AgentCheckpoint).where(
            AgentCheckpoint.session_id == session_id,
            AgentCheckpoint.checkpoint_no < checkpoint_no,
            AgentCheckpoint.status == "ready",
        )
        checkpoints = self.session.execute(stmt).scalars().all()
        count = 0
        for checkpoint in checkpoints:
            checkpoint.status = "superseded"
            count += 1
        self.session.flush()
        return count

    def purge_expired(self, now: Optional[datetime] = None) -> int:
        """Purge expired checkpoints."""
        now = now or utc_now()
        stmt = select(AgentCheckpoint).where(
            AgentCheckpoint.status == "ready",
            AgentCheckpoint.expires_at.isnot(None),
            AgentCheckpoint.expires_at < now,
        )
        checkpoints = self.session.execute(stmt).scalars().all()
        count = 0
        for checkpoint in checkpoints:
            checkpoint.status = "purged"
            checkpoint.state_blob = b""
            count += 1
        self.session.flush()
        return count

    def delete_for_session(self, session_id: str) -> int:
        """Delete all checkpoints for a session."""
        stmt = select(AgentCheckpoint).where(
            AgentCheckpoint.session_id == session_id
        )
        checkpoints = self.session.execute(stmt).scalars().all()
        count = 0
        for checkpoint in checkpoints:
            self.session.delete(checkpoint)
            count += 1
        self.session.flush()
        return count
