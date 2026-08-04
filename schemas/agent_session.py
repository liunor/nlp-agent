"""Agent session-related Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class AgentSessionBase(BaseModel):
    """Base agent session schema."""

    title: str = Field(min_length=1, max_length=255)


class AgentSessionCreate(AgentSessionBase):
    """Schema for creating a new agent session."""

    workspace_id: str
    model_profile_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentSessionUpdate(BaseModel):
    """Schema for updating agent session."""

    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    status: Optional[str] = Field(default=None, pattern="^(active|archived)$")


class AgentSessionResponse(AgentSessionBase):
    """Schema for agent session response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    created_by_user_id: str
    status: str
    active_turn_id: Optional[str] = None
    model_profile_id: Optional[str] = None
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class AgentSessionListResponse(BaseModel):
    """Schema for agent session list."""

    items: list[AgentSessionResponse]
