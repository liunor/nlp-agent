"""Pydantic schemas for user management."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .identity import validate_public_identity_id
from .phone import normalize_phone_number


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
    identity_id: Optional[str] = Field(None, min_length=1, max_length=32)
    status: Optional[str] = Field(None, pattern="^(active|disabled|locked)$")

    @field_validator("identity_id")
    @classmethod
    def validate_identity_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return validate_public_identity_id(value)


class UserResponse(BaseModel):
    """User response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    identity_id: str
    username: str
    display_name: str
    status: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    last_login_at: datetime | None = None
    roles: list[str] = Field(default_factory=list)


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


class PasswordConfirm(BaseModel):
    """Schema for self-service account deletion password confirmation."""

    password: str = Field(..., min_length=1, max_length=128)


class UserCreateWithRole(UserCreate):
    """Schema for admin creating a user with role assignment."""

    role_codes: list[str] = Field(default_factory=list, max_length=4)


class EmailUserRegister(BaseModel):
    """Public registration accepts only an email and its verification code."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=5, max_length=254)
    email_code: str = Field(..., min_length=4, max_length=8)
    password: str = Field(..., min_length=8, max_length=128)
    display_name: Optional[str] = Field(None, min_length=1, max_length=128)
    captcha_id: str = Field(..., min_length=1)
    captcha_code: str = Field(..., min_length=1, max_length=10)


class UserRegister(BaseModel):
    """Internal registration input; retains compatibility with legacy phone identities.

    The public HTTP endpoint uses EmailUserRegister, so legacy SMS codes cannot
    be used to create a phone account through that endpoint.
    """

    phone_number: Optional[str] = Field(None, min_length=5, max_length=20, pattern=r"^\+?\d[\d\s-]*$")
    sms_code: Optional[str] = Field(None, min_length=4, max_length=8)
    email: Optional[str] = Field(None, min_length=5, max_length=254)
    email_code: Optional[str] = Field(None, min_length=4, max_length=8)
    password: str = Field(..., min_length=8, max_length=128)
    display_name: Optional[str] = Field(None, min_length=1, max_length=128)
    captcha_id: str = Field(..., min_length=1)
    captcha_code: str = Field(..., min_length=1, max_length=10)

    @field_validator("phone_number")
    @classmethod
    def normalize_phone_identity(cls, value: str | None) -> str | None:
        return normalize_phone_number(value) if value is not None else None

    @model_validator(mode="after")
    def validate_registration_identity(self) -> "UserRegister":
        has_phone = self.phone_number is not None
        has_email = self.email is not None
        if has_phone == has_email:
            raise ValueError("provide exactly one of phone_number or email")
        if has_phone != (self.sms_code is not None):
            raise ValueError("phone_number and sms_code must be provided together")
        if has_email != (self.email_code is not None):
            raise ValueError("email and email_code must be provided together")
        return self


class EmailCodeRequest(BaseModel):
    """Schema for requesting an email verification code."""

    email: str = Field(..., min_length=5, max_length=254)
    captcha_id: str = Field(..., min_length=1)
    captcha_code: str = Field(..., min_length=1, max_length=10)


class SmsCodeRequest(BaseModel):
    """Schema for requesting an SMS verification code."""

    phone_number: str = Field(..., min_length=5, max_length=20, pattern=r"^\+?\d[\d\s-]*$")
    captcha_id: str = Field(..., min_length=1)
    captcha_code: str = Field(..., min_length=1, max_length=10)

    @field_validator("phone_number")
    @classmethod
    def normalize_phone_identity(cls, value: str) -> str:
        return normalize_phone_number(value)


class UserRoleUpdate(BaseModel):
    """Schema for assigning/replacing user roles (admin only)."""

    role_codes: list[str] = Field(default_factory=list, max_length=4)
