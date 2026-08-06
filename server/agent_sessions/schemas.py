"""Pydantic schemas for agent sessions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class AgentSessionBase(BaseModel):
    """Base agent session schema."""

    title: str = Field(..., min_length=1, max_length=255)


class AgentSessionCreate(AgentSessionBase):
    """Schema for creating an agent session."""

    metadata: Optional[dict[str, Any]] = Field(default_factory=dict)


class AgentSessionUpdate(BaseModel):
    """Schema for updating an agent session."""

    title: Optional[str] = Field(None, min_length=1, max_length=255)
    status: Optional[str] = Field(None, pattern="^(active|archived|deleted)$")


class AgentSessionResponse(BaseModel):
    """Agent session response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    created_by_user_id: str
    title: str
    status: str
    active_conversation_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AgentSessionListResponse(BaseModel):
    """List of agent sessions."""

    sessions: list[AgentSessionResponse]
    total: int
