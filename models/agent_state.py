"""Agent state models: workspaces, sessions, turns, events, checkpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from db.database import Base, generate_uuid7, utc_now


class Workspace(Base):
    """Data isolation container for agent resources."""

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid7
    )
    type: Mapped[str] = mapped_column(
        Enum("personal", "organization", "classroom", name="workspace_type"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("active", "suspended", "deleted", name="workspace_status"),
        nullable=False,
        default="active",
    )
    created_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    members: Mapped[list[WorkspaceMember]] = relationship(
        "WorkspaceMember", back_populates="workspace", cascade="all, delete-orphan"
    )
    agent_sessions: Mapped[list[AgentSession]] = relationship(
        "AgentSession", back_populates="workspace"
    )

    @property
    def is_active(self) -> bool:
        return self.status == "active"


class WorkspaceMember(Base):
    """User membership and role within a workspace."""

    __tablename__ = "workspace_members"

    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Enum("active", "invited", "removed", name="member_status"),
        nullable=False,
        default="active",
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    workspace: Mapped[Workspace] = relationship("Workspace", back_populates="members")

    __table_args__ = (
        Index("ix_workspace_members_user", "user_id", "status"),
    )


class AgentSession(Base):
    """Model conversation/learning session within a workspace."""

    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid7
    )
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("active", "archived", "deleted", name="session_status"),
        nullable=False,
        default="active",
    )
    active_turn_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True
    )
    model_profile_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    workspace: Mapped[Workspace] = relationship(
        "Workspace", back_populates="agent_sessions"
    )
    turns: Mapped[list[Turn]] = relationship(
        "Turn", back_populates="session", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index(
            "ix_agent_sessions_workspace_updated",
            "workspace_id",
            "status",
            "updated_at",
        ),
        Index("ix_agent_sessions_creator", "created_by_user_id", "created_at"),
    )


class Turn(Base):
    """Single model execution within a session."""

    __tablename__ = "turns"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid7
    )
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    submitted_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(36), nullable=False
    )
    state: Mapped[str] = mapped_column(
        Enum(
            "accepted",
            "queued",
            "running",
            "cancelling",
            "completed",
            "cancelled",
            "failed",
            "interrupted",
            name="turn_state",
        ),
        nullable=False,
        default="accepted",
    )
    input_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False
    )
    output_summary: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )
    cancel_requested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    session: Mapped[AgentSession] = relationship(
        "AgentSession", back_populates="turns"
    )
    events: Mapped[list[TurnEvent]] = relationship(
        "TurnEvent", back_populates="turn", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index(
            "uk_turn_idempotency",
            "workspace_id",
            "session_id",
            "idempotency_key",
            unique=True,
        ),
        Index("ix_turns_session_state", "session_id", "state", "created_at"),
        Index("ix_turns_workspace_created", "workspace_id", "created_at"),
    )


class TurnEvent(Base):
    """Replayable execution event within a turn."""

    __tablename__ = "turn_events"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid7
    )
    turn_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("turns.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    turn: Mapped[Turn] = relationship("Turn", back_populates="events")

    __table_args__ = (
        Index("uk_turn_event_sequence", "turn_id", "sequence", unique=True),
        Index(
            "uk_turn_event_idempotency",
            "turn_id",
            "idempotency_key",
            unique=True,
        ),
        Index("ix_turn_events_replay", "turn_id", "sequence"),
    )


class AgentCheckpoint(Base):
    """LangGraph/model execution state checkpoint."""

    __tablename__ = "agent_checkpoints"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid7
    )
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    turn_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("turns.id", ondelete="SET NULL"), nullable=True
    )
    checkpoint_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state_version: Mapped[str] = mapped_column(String(32), nullable=False)
    state_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    state_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    encrypted_data_key: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary(512), nullable=True
    )
    status: Mapped[str] = mapped_column(
        Enum("ready", "superseded", "purged", name="checkpoint_status"),
        nullable=False,
        default="ready",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("uk_checkpoint_number", "session_id", "checkpoint_no", unique=True),
        Index("ix_checkpoints_turn", "turn_id", "created_at"),
        Index("ix_checkpoints_workspace_expiry", "workspace_id", "expires_at"),
    )


class ExecutionLease(Base):
    """Worker execution lease for turn processing."""

    __tablename__ = "execution_leases"

    turn_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("turns.id", ondelete="CASCADE"),
        primary_key=True,
    )
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    lease_token: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        Index("uk_lease_token", "lease_token", unique=True),
        Index("ix_leases_expiry", "expires_at"),
    )


class OutboxMessage(Base):
    """Transaction outbox for reliable event delivery."""

    __tablename__ = "outbox_messages"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid7
    )
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_outbox_unpublished", "published_at", "occurred_at"),
    )
