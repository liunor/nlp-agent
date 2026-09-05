"""User management module.

Provides user CRUD operations and profile management,
integrated with the existing RBAC infrastructure.
"""

from server.user.service import (
    InvalidCaptchaError,
    InvalidSmsCodeError,
    PhoneNumberAlreadyUsedError,
    UserAlreadyExistsError,
    UserService,
    UserServiceError,
)
from server.user.schemas import (
    UserCreate,
    UserUpdate,
    UserResponse,
    UserListResponse,
)

__all__ = [
    "UserService",
    "UserServiceError",
    "UserAlreadyExistsError",
    "PhoneNumberAlreadyUsedError",
    "InvalidCaptchaError",
    "InvalidSmsCodeError",
    "UserCreate",
    "UserUpdate",
    "UserResponse",
    "UserListResponse",
]
