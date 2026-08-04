"""Auth session CRUD operations."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from db.database import generate_uuid7, utc_now
from models.identity import AuthSession


class AuthSessionCRUD:
    """CRUD operations for AuthSession model."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        user_id: str,
        token_hash: str,
        csrf_secret_hash: str,
        user_token_version: int,
        expires_at: datetime,
        ip_prefix: Optional[bytes] = None,
        user_agent_hash: Optional[bytes] = None,
    ) -> AuthSession:
        """Create a new auth session."""
        auth_session = AuthSession(
            id=generate_uuid7(),
            user_id=user_id,
            token_hash=token_hash,
            csrf_secret_hash=csrf_secret_hash,
            user_token_version=user_token_version,
            expires_at=expires_at,
            last_seen_at=utc_now(),
            ip_prefix=ip_prefix,
            user_agent_hash=user_agent_hash,
        )
        self.session.add(auth_session)
        self.session.flush()
        return auth_session

    def get_by_id(self, session_id: str) -> Optional[AuthSession]:
        """Get auth session by ID."""
        stmt = select(AuthSession).where(AuthSession.id == session_id)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_by_token_hash(self, token_hash: str) -> Optional[AuthSession]:
        """Get auth session by token hash."""
        stmt = select(AuthSession).where(AuthSession.token_hash == token_hash)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_valid_session(
        self,
        *,
        user_id: str,
        token_hash: str,
        now: Optional[datetime] = None,
    ) -> Optional[AuthSession]:
        """Get a valid (not revoked, not expired) auth session."""
        now = now or utc_now()
        stmt = select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.token_hash == token_hash,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def update_last_seen(self, session_id: str) -> None:
        """Update the last seen timestamp."""
        stmt = (
            update(AuthSession)
            .where(AuthSession.id == session_id)
            .values(last_seen_at=utc_now())
        )
        self.session.execute(stmt)

    def revoke(
        self,
        session_id: str,
        *,
        reason: Optional[str] = None,
    ) -> bool:
        """Revoke an auth session."""
        auth_session = self.get_by_id(session_id)
        if auth_session is None or auth_session.revoked_at is not None:
            return False

        auth_session.revoked_at = utc_now()
        auth_session.revoke_reason = reason
        self.session.flush()
        return True

    def revoke_all_for_user(
        self,
        user_id: str,
        *,
        reason: Optional[str] = None,
        except_session_id: Optional[str] = None,
    ) -> int:
        """Revoke all auth sessions for a user."""
        stmt = select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
        if except_session_id:
            stmt = stmt.where(AuthSession.id != except_session_id)

        sessions = self.session.execute(stmt).scalars().all()
        now = utc_now()
        count = 0
        for auth_session in sessions:
            auth_session.revoked_at = now
            auth_session.revoke_reason = reason
            count += 1
        self.session.flush()
        return count

    def revoke_by_token_version(
        self,
        user_id: str,
        *,
        current_version: int,
        reason: Optional[str] = None,
    ) -> int:
        """Revoke sessions with outdated token versions."""
        stmt = select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.user_token_version < current_version,
            AuthSession.revoked_at.is_(None),
        )
        sessions = self.session.execute(stmt).scalars().all()
        now = utc_now()
        count = 0
        for auth_session in sessions:
            auth_session.revoked_at = now
            auth_session.revoke_reason = reason or "token_version_changed"
            count += 1
        self.session.flush()
        return count

    def list_user_sessions(
        self,
        user_id: str,
        *,
        active_only: bool = True,
    ) -> list[AuthSession]:
        """List auth sessions for a user."""
        stmt = select(AuthSession).where(AuthSession.user_id == user_id)
        if active_only:
            now = utc_now()
            stmt = stmt.where(
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
            )
        stmt = stmt.order_by(AuthSession.last_seen_at.desc())
        return list(self.session.execute(stmt).scalars().all())
