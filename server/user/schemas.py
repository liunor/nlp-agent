"""Pydantic schemas for user management."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserBase(BaseModel):
    """Base user schema with common fields."""

    username: str = Field(..., min_length=3, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if not v.isalnum():
            raise ValueError("username must be alphanumeric")
        return v.lower()


class UserCreate(UserBase):
    """Schema for creating a new user."""

    password: str = Field(..., min_length=8, max_length=128)


class UserUpdate(BaseModel):
    """Schema for updating user profile (self-service)."""

    display_name: Optional[str] = Field(None, min_length=1, max_length=128)


class UserAdminUpdate(BaseModel):
    """Schema for admin user updates."""

    display_name: Optional[str] = Field(None, min_length=1, max_length=128)
    status: Optional[str] = Field(None, pattern="^(active|disabled|locked)$")


class UserResponse(BaseModel):
    """User response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: str
    status: str
    created_at: datetime
    updated_at: datetime


class UserListResponse(BaseModel):
    """Paginated user list response."""

    users: list[UserResponse]
    total: int
    offset: int
    limit: int


class PasswordChange(BaseModel):
    """Schema for password change."""

    current_password: str
    new_password: str = Field(..., min_length=8, max_length=128)


class PasswordReset(BaseModel):
    """Schema for admin password reset."""

    new_password: str = Field(..., min_length=8, max_length=128)
