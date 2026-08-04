"""Authentication-related Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """Schema for login request."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class LoginResponse(BaseModel):
    """Schema for login response."""

    user_id: str
    username: str
    display_name: str
    workspace_ids: list[str]
    roles: list[str]
    csrf_token: str
    expires_at: datetime


class SessionInfo(BaseModel):
    """Schema for current session info."""

    model_config = ConfigDict(from_attributes=True)

    user_id: str
    username: str
    display_name: str
    workspace_ids: list[str]
    roles: list[str]
    csrf_token: str
    expires_at: datetime


class WorkspacePrincipalData(BaseModel):
    """Schema for workspace principal in responses."""

    user_id: str
    auth_session_id: str
    workspace_id: str
    system_roles: list[str]
    workspace_role: Optional[str] = None
