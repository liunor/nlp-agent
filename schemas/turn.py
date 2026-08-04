"""Turn-related Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class TurnResponse(BaseModel):
    """Schema for turn response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    session_id: str
    submitted_by_user_id: str
    idempotency_key: str
    state: str
    input_payload: dict[str, Any]
    output_summary: Optional[dict[str, Any]] = None
    cancel_requested_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    version: int
    created_at: datetime
    updated_at: datetime


class TurnListResponse(BaseModel):
    """Schema for turn list."""

    items: list[TurnResponse]


class TurnEventResponse(BaseModel):
    """Schema for turn event response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    turn_id: str
    sequence: int
    type: str
    idempotency_key: Optional[str] = None
    payload: dict[str, Any]
    created_at: datetime


class TurnEventListResponse(BaseModel):
    """Schema for turn event list."""

    items: list[TurnEventResponse]


class TurnCancelRequest(BaseModel):
    """Schema for turn cancel request."""

    reason: Optional[str] = Field(default=None, max_length=255)
