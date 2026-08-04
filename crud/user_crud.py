"""User CRUD operations."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select, func, update
from sqlalchemy.orm import Session

from db.database import generate_uuid7, utc_now
from models.identity import User


class UserCRUD:
    """CRUD operations for User model."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        username: str,
        password_hash: str,
        display_name: str,
        email: Optional[str] = None,
    ) -> User:
        """Create a new user."""
        user = User(
            id=generate_uuid7(),
            username=username,
            email=email,
            password_hash=password_hash,
            display_name=display_name,
            status="active",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.session.add(user)
        self.session.flush()
        return user

    def get_by_id(self, user_id: str) -> Optional[User]:
        """Get user by ID."""
        stmt = select(User).where(User.id == user_id, User.deleted_at.is_(None))
        return self.session.execute(stmt).scalar_one_or_none()

    def get_by_username(self, username: str) -> Optional[User]:
        """Get user by username."""
        stmt = select(User).where(
            User.username == username, User.deleted_at.is_(None)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def get_by_email(self, email: str) -> Optional[User]:
        """Get user by email."""
        stmt = select(User).where(User.email == email, User.deleted_at.is_(None))
        return self.session.execute(stmt).scalar_one_or_none()

    def list_users(
        self, *, offset: int = 0, limit: int = 100
    ) -> tuple[list[User], int]:
        """List users with pagination."""
        count_stmt = select(func.count()).select_from(User).where(User.deleted_at.is_(None))
        total = self.session.execute(count_stmt).scalar() or 0

        stmt = (
            select(User)
            .where(User.deleted_at.is_(None))
            .order_by(User.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        users = list(self.session.execute(stmt).scalars().all())
        return users, total

    def update(
        self,
        user_id: str,
        *,
        display_name: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Optional[User]:
        """Update user profile."""
        user = self.get_by_id(user_id)
        if user is None:
            return None

        if display_name is not None:
            user.display_name = display_name
        if email is not None:
            user.email = email
        user.updated_at = utc_now()

        self.session.flush()
        return user

    def update_status(self, user_id: str, status: str) -> Optional[User]:
        """Update user status."""
        user = self.get_by_id(user_id)
        if user is None:
            return None

        user.status = status
        user.updated_at = utc_now()
        if status == "deleted":
            user.deleted_at = utc_now()

        self.session.flush()
        return user

    def increment_token_version(self, user_id: str) -> int:
        """Increment token version to invalidate existing sessions."""
        user = self.get_by_id(user_id)
        if user is None:
            return 0

        user.token_version += 1
        user.updated_at = utc_now()
        self.session.flush()
        return user.token_version

    def update_last_login(self, user_id: str) -> None:
        """Update last login timestamp."""
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(last_login_at=utc_now())
        )
        self.session.execute(stmt)

    def soft_delete(self, user_id: str) -> bool:
        """Soft delete a user."""
        user = self.get_by_id(user_id)
        if user is None:
            return False

        user.status = "deleted"
        user.deleted_at = utc_now()
        user.updated_at = utc_now()
        self.session.flush()
        return True
