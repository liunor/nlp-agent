"""Authentication service for login/logout and session management."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy.orm import Session

from crud.auth_session_crud import AuthSessionCRUD
from crud.role_crud import RoleCRUD
from crud.user_crud import UserCRUD
from crud.workspace_crud import WorkspaceCRUD
from models.identity import User


@dataclass
class LoginResult:
    """Result of a successful login."""

    user: User
    auth_session_id: str
    session_token: str
    csrf_token: str
    expires_at: datetime
    workspace_ids: list[str]
    system_roles: list[str]


class AuthenticationError(PermissionError):
    """Raised when authentication fails."""

    pass


class AuthService:
    """Service for authentication operations."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.user_crud = UserCRUD(session)
        self.auth_session_crud = AuthSessionCRUD(session)
        self.role_crud = RoleCRUD(session)
        self.workspace_crud = WorkspaceCRUD(session)
        self._password_hasher = PasswordHasher()

    def _hash_password(self, password: str) -> str:
        """Hash a password using Argon2id."""
        return self._password_hasher.hash(password)

    def _verify_password(self, password: str, password_hash: str) -> bool:
        """Verify a password against its hash."""
        try:
            return self._password_hasher.verify(password_hash, password)
        except VerifyMismatchError:
            return False

    def _hash_token(self, token: str) -> str:
        """Hash a session token for storage."""
        return hashlib.sha256(token.encode()).hexdigest()

    def _generate_csrf_token(self) -> str:
        """Generate a CSRF token."""
        return secrets.token_urlsafe(32)

    def login(
        self,
        *,
        username: str,
        password: str,
        ip_prefix: Optional[bytes] = None,
        user_agent_hash: Optional[bytes] = None,
        session_ttl_hours: int = 24,
    ) -> LoginResult:
        """Authenticate user and create a new session.

        Raises AuthenticationError if credentials are invalid.
        """
        user = self.user_crud.get_by_username(username)
        if user is None:
            raise AuthenticationError("Invalid username or password")

        if not user.is_active:
            raise AuthenticationError("User account is not active")

        if not self._verify_password(password, user.password_hash):
            raise AuthenticationError("Invalid username or password")

        system_roles = list(self.role_crud.get_user_role_codes(user.id))

        workspaces = self.workspace_crud.list_workspaces_for_user(user.id)
        workspace_ids = [ws.id for ws in workspaces]

        session_token = secrets.token_urlsafe(48)
        csrf_token = self._generate_csrf_token()
        token_hash = self._hash_token(session_token)
        csrf_secret_hash = self._hash_token(csrf_token)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=session_ttl_hours)

        auth_session = self.auth_session_crud.create(
            user_id=user.id,
            token_hash=token_hash,
            csrf_secret_hash=csrf_secret_hash,
            user_token_version=user.token_version,
            expires_at=expires_at,
            ip_prefix=ip_prefix,
            user_agent_hash=user_agent_hash,
        )

        self.user_crud.update_last_login(user.id)

        return LoginResult(
            user=user,
            auth_session_id=auth_session.id,
            session_token=session_token,
            csrf_token=csrf_token,
            expires_at=expires_at,
            workspace_ids=workspace_ids,
            system_roles=system_roles,
        )

    def validate_session(
        self,
        *,
        session_token: str,
    ) -> Optional[LoginResult]:
        """Validate a session token and return user info.

        Returns None if the session is invalid.
        """
        token_hash = self._hash_token(session_token)
        auth_session = self.auth_session_crud.get_by_token_hash(token_hash)

        if auth_session is None:
            return None

        if not auth_session.is_valid:
            return None

        user = self.user_crud.get_by_id(auth_session.user_id)
        if user is None or not user.is_active:
            return None

        if auth_session.user_token_version != user.token_version:
            return None

        system_roles = list(self.role_crud.get_user_role_codes(user.id))
        workspaces = self.workspace_crud.list_workspaces_for_user(user.id)
        workspace_ids = [ws.id for ws in workspaces]

        csrf_token = auth_session.csrf_secret_hash

        self.auth_session_crud.update_last_seen(auth_session.id)

        return LoginResult(
            user=user,
            auth_session_id=auth_session.id,
            session_token=session_token,
            csrf_token=csrf_token,
            expires_at=auth_session.expires_at,
            workspace_ids=workspace_ids,
            system_roles=system_roles,
        )

    def logout(self, auth_session_id: str) -> bool:
        """Logout and revoke a session."""
        return self.auth_session_crud.revoke(
            auth_session_id, reason="user_logout"
        )

    def logout_all_for_user(
        self, user_id: str, except_session_id: Optional[str] = None
    ) -> int:
        """Revoke all sessions for a user."""
        return self.auth_session_crud.revoke_all_for_user(
            user_id,
            reason="user_logout_all",
            except_session_id=except_session_id,
        )

    def change_password(
        self,
        user_id: str,
        *,
        current_password: str,
        new_password: str,
    ) -> bool:
        """Change user password and invalidate all sessions."""
        user = self.user_crud.get_by_id(user_id)
        if user is None:
            return False

        if not self._verify_password(current_password, user.password_hash):
            return False

        user.password_hash = self._hash_password(new_password)
        new_version = self.user_crud.increment_token_version(user_id)
        self.auth_session_crud.revoke_by_token_version(
            user_id, current_version=new_version, reason="password_changed"
        )
        return True

    def reset_password(self, user_id: str, new_password: str) -> bool:
        """Reset user password (admin operation)."""
        user = self.user_crud.get_by_id(user_id)
        if user is None:
            return False

        user.password_hash = self._hash_password(new_password)
        new_version = self.user_crud.increment_token_version(user_id)
        self.auth_session_crud.revoke_all_for_user(
            user_id, reason="password_reset_by_admin"
        )
        return True

    def create_user(
        self,
        *,
        username: str,
        password: str,
        display_name: str,
        email: Optional[str] = None,
    ) -> User:
        """Create a new user with hashed password."""
        existing = self.user_crud.get_by_username(username)
        if existing is not None:
            raise ValueError(f"Username '{username}' is already taken")

        if email:
            existing_email = self.user_crud.get_by_email(email)
            if existing_email is not None:
                raise ValueError(f"Email '{email}' is already registered")

        password_hash = self._hash_password(password)
        user = self.user_crud.create(
            username=username,
            password_hash=password_hash,
            display_name=display_name,
            email=email,
        )
        return user
