"""User-related Pydantic schemas for request/response DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserBase(BaseModel):
    """Base user schema with common fields."""

    username: str = Field(min_length=3, max_length=64)
    email: Optional[EmailStr] = None
    display_name: str = Field(min_length=1, max_length=128)


class UserCreate(UserBase):
    """Schema for creating a new user."""

    password: str = Field(min_length=8, max_length=128)


class UserUpdate(BaseModel):
    """Schema for updating user profile (self-service)."""

    email: Optional[EmailStr] = None
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=128)


class UserAdminUpdate(BaseModel):
    """Schema for admin user updates (includes status and roles)."""

    display_name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    email: Optional[EmailStr] = None
    status: Optional[str] = Field(default=None, pattern="^(active|disabled|locked)$")


class UserResponse(UserBase):
    """Schema for user response data."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    created_at: datetime
    updated_at: datetime
    last_login_at: Optional[datetime] = None


class UserListResponse(BaseModel):
    """Schema for paginated user list."""

    items: list[UserResponse]
    total: int


class PasswordReset(BaseModel):
    """Schema for password reset request."""

    new_password: str = Field(min_length=8, max_length=128)


class PasswordChange(BaseModel):
    """Schema for self-service password change."""

    current_password: str
    new_password: str = Field(min_length=8, max_length=128)
