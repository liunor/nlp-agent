"""Pydantic schemas for authentication.

Covers the supplementary auth endpoints (GET /me, POST /password,
session management).  The primary login/logout flow uses the existing
SameOriginSessionAuth schemas in server/web/contracts.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Login request schema (used by new auth endpoints if needed)."""

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class LoginResponse(BaseModel):
    """Login response schema."""

    user_id: str
    username: str
    display_name: str
    csrf_token: str
    expires_at: int  # Unix timestamp from HMAC session


class MeResponse(BaseModel):
    """Current user profile with RBAC context.

    Combines DB user data with HMAC session claims to give the
    frontend a complete identity snapshot.
    """

    user_id: str
    username: str
    display_name: str
    status: str
    roles: list[str]
    workspace_ids: list[str]
    permissions: list[str]
    created_at: datetime
    updated_at: datetime


class PasswordChangeRequest(BaseModel):
    """Password change request."""

    current_password: str
    new_password: str = Field(..., min_length=8, max_length=128)


class ActiveSessionItem(BaseModel):
    """A single active session record."""

    id: str
    user_id: str
    workspace_id: str
    expires_at: datetime
    revoked_at: Optional[datetime] = None
    created_at: datetime


class ActiveSessionListResponse(BaseModel):
    """List of active DB sessions."""

    sessions: list[ActiveSessionItem]


class RevokeSessionsRequest(BaseModel):
    """Request to revoke all sessions for a user."""

    user_id: str


class RevokeSessionsResponse(BaseModel):
    """Result of session revocation."""

    revoked_count: int


class CsrfTokenResponse(BaseModel):
    """CSRF token response."""

    csrf_token: str
    expires_at: datetime
