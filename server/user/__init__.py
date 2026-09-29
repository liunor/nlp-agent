"""User management module.

Provides user CRUD operations and profile management,
integrated with the existing RBAC infrastructure.
"""

from server.user.service import (
    EmailAlreadyUsedError,
    InvalidCaptchaError,
    InvalidEmailCodeError,
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
    "EmailAlreadyUsedError",
    "InvalidCaptchaError",
    "InvalidEmailCodeError",
    "UserCreate",
    "UserUpdate",
    "UserResponse",
    "UserListResponse",
]
