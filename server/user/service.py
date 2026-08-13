"""User management service.

Integrates with existing RBAC infrastructure for role management
and uses the shared MySQL async session factory.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.infrastructure.mysql.models import UserModel, WorkspaceModel, WorkspaceMemberModel

from .schemas import UserCreate, UserUpdate


class UserServiceError(Exception):
    """Base error for user service operations."""


class UserNotFoundError(UserServiceError):
    """Raised when a user is not found."""


class UserAlreadyExistsError(UserServiceError):
    """Raised when attempting to create a duplicate user."""


class PasswordHasherSingleton:
    """Singleton password hasher with tuned Argon2id parameters."""

    _instance: Optional[PasswordHasher] = None

    @classmethod
    def get(cls) -> PasswordHasher:
        if cls._instance is None:
            # Tuned parameters for security (2026 recommendations)
            cls._instance = PasswordHasher(
                time_cost=4,        # 4 iterations
                memory_cost=65536,  # 64 MB
                parallelism=4,      # 4 threads
                hash_len=32,
                salt_len=16,
                type=Type.ID,  # Argon2id
            )
        return cls._instance


class UserService:
    """Service for user management operations.

    This service handles user CRUD operations while integrating with
    the existing RBAC infrastructure for role assignments.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.hasher = PasswordHasherSingleton.get()

    async def create_user(
        self,
        data: UserCreate,
        *,
        actor_user_id: Optional[str] = None,
    ) -> UserModel:
        """Create a new user with password hashing.

        This operation also creates a personal workspace for the user
        and assigns them as the owner.
        """
        # Check for existing user
        existing = await self.session.scalar(
            select(UserModel.id).where(
                UserModel.username == data.username
            )
        )
        if existing:
            raise UserAlreadyExistsError(
                "User with this username already exists"
            )

        # Hash password
        password_hash = self.hasher.hash(data.password)

        # Create user
        user = UserModel(
            id=str(uuid.uuid4()),
            username=data.username,
            password_hash=password_hash,
            display_name=data.display_name,
            status="active",
            authorization_version=1,
        )
        self.session.add(user)

        # Create personal workspace
        workspace = WorkspaceModel(
            id=str(uuid.uuid4()),
            slug=f"user-{data.username}",
            name=f"{data.display_name}'s Workspace",
            status="active",
        )
        self.session.add(workspace)

        # Add user as workspace owner
        member = WorkspaceMemberModel(
            workspace_id=workspace.id,
            user_id=user.id,
            member_type="owner",
            status="active",
        )
        self.session.add(member)

        await self.session.flush()
        return user

    async def get_user(self, user_id: str) -> UserModel:
        """Get user by ID."""
        user = await self.session.scalar(
            select(UserModel).where(UserModel.id == user_id)
        )
        if user is None:
            raise UserNotFoundError(f"User {user_id} not found")
        return user

    async def get_user_by_username(self, username: str) -> Optional[UserModel]:
        """Get user by username."""
        return await self.session.scalar(
            select(UserModel).where(UserModel.username == username)
        )

    async def list_users(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        status: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> tuple[list[UserModel], int]:
        """List users with pagination."""
        query = select(UserModel)
        count_query = select(func.count()).select_from(UserModel)

        if status:
            query = query.where(UserModel.status == status)
            count_query = count_query.where(UserModel.status == status)

        if keyword:
            pattern = f"%{keyword}%"
            like_filter = or_(UserModel.username.ilike(pattern), UserModel.display_name.ilike(pattern))
            query = query.where(like_filter)
            count_query = count_query.where(like_filter)

        query = query.order_by(UserModel.created_at.desc()).offset(offset).limit(limit)

        users = list(await self.session.scalars(query))
        total = await self.session.scalar(count_query)

        return users, total or 0

    async def update_user(
        self,
        user_id: str,
        data: UserUpdate,
    ) -> UserModel:
        """Update user profile."""
        user = await self.get_user(user_id)

        if data.display_name is not None:
            user.display_name = data.display_name

        user.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await self.session.flush()
        return user

    async def update_user_status(
        self,
        user_id: str,
        status: str,
        *,
        actor_user_id: str,
    ) -> UserModel:
        """Update user status (admin operation)."""
        user = await self.get_user(user_id)
        user.status = status
        user.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

        # Bump authorization version to invalidate sessions
        user.authorization_version += 1

        await self.session.flush()
        return user

    async def verify_password(self, user: UserModel, password: str) -> bool:
        """Verify a password against the stored hash."""
        try:
            return self.hasher.verify(user.password_hash, password)
        except VerifyMismatchError:
            return False

    async def change_password(
        self,
        user_id: str,
        new_password: str,
    ) -> UserModel:
        """Change user password."""
        user = await self.get_user(user_id)
        user.password_hash = self.hasher.hash(new_password)
        user.authorization_version += 1  # Invalidate all sessions
        user.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await self.session.flush()
        return user

    async def update_last_login(self, user_id: str) -> None:
        """Update the last login timestamp.
        
        Note: This requires a migration to add last_login_at to nlp_users.
        For now, this is a no-op placeholder.
        """
        # TODO: Add last_login_at column via migration
        pass
