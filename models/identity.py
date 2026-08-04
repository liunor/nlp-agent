"""Identity and authentication models: users, roles, auth_sessions."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.database import Base, generate_uuid7, utc_now


class User(Base):
    """User account with authentication and status tracking."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid7
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    email: Mapped[Optional[str]] = mapped_column(String(254), nullable=True, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("active", "disabled", "locked", "deleted", name="user_status"),
        nullable=False,
        default="active",
    )
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user_roles: Mapped[list[UserRole]] = relationship(
        "UserRole", back_populates="user", cascade="all, delete-orphan"
    )
    auth_sessions: Mapped[list[AuthSession]] = relationship(
        "AuthSession", back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_users_username", "username"),
        Index("ix_users_email", "email"),
    )

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def is_deleted(self) -> bool:
        return self.status == "deleted" or self.deleted_at is not None


class Role(Base):
    """System or workspace-level role definition."""

    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid7
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[str] = mapped_column(
        Enum("system", "workspace", name="role_scope"),
        nullable=False,
    )

    user_roles: Mapped[list[UserRole]] = relationship(
        "UserRole", back_populates="role"
    )

    def __repr__(self) -> str:
        return f"<Role(code={self.code!r}, scope={self.scope!r})>"


class UserRole(Base):
    """Association between users and roles."""

    __tablename__ = "user_roles"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )

    user: Mapped[User] = relationship("User", back_populates="user_roles")
    role: Mapped[Role] = relationship("Role", back_populates="user_roles")


class AuthSession(Base):
    """Browser login session with token hash and expiry."""

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid7
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    csrf_secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_token_version: Mapped[int] = mapped_column(Integer, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoke_reason: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    ip_prefix: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary(16), nullable=True
    )
    user_agent_hash: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary(32), nullable=True
    )

    user: Mapped[User] = relationship("User", back_populates="auth_sessions")

    __table_args__ = (
        Index("ix_auth_sessions_active", "user_id", "revoked_at", "expires_at"),
    )

    @property
    def is_valid(self) -> bool:
        """Check if the session is still valid (not revoked and not expired)."""
        from datetime import timezone
        now = utc_now()
        # Ensure both datetimes are timezone-aware for comparison
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return self.revoked_at is None and expires_at > now
