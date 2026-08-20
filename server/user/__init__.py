"""User management module.

Provides user CRUD operations and profile management,
integrated with the existing RBAC infrastructure.
"""

from server.user.service import UserService, UserServiceError
from server.user.schemas import (
    UserCreate,
    UserUpdate,
    UserResponse,
    UserListResponse,
)

__all__ = [
    "UserService",
    "UserServiceError",
    "UserCreate",
    "UserUpdate",
    "UserResponse",
    "UserListResponse",
]
