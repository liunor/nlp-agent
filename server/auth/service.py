"""DB session management auxiliary for authentication.

The primary authentication flow is handled by SameOriginSessionAuth
(server/web/auth.py) using HMAC-signed stateless cookies.

This service provides *supplementary* DB-backed session management:
  - Recording login sessions for audit evidence
  - Querying active sessions for admin dashboards
  - Revoking sessions (admin forced logout)

It does NOT handle login/logout authentication decisions.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.identity import AuthenticatedPrincipal
from server.infrastructure.mysql.models import (
    SessionModel,
    UserModel,
    WorkspaceMemberModel,
)
from server.rbac.service import RbacService

from server.user.service import PasswordHasherSingleton


# Session duration: 7 days
SESSION_DURATION = timedelta(days=7)


def hash_token(token: str) -> str:
    """Hash a session token using SHA-256."""
    return hashlib.sha256(token.encode()).hexdigest()


class AuthService:
    """DB session management auxiliary.

    Complements the HMAC-based SameOriginSessionAuth with persistent
    session records for audit and admin management.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.rbac_service = RbacService()

    async def record_login_session(
        self,
        *,
        user_id: str,
        workspace_id: str,
        token: str,
        csrf_hash: str,
    ) -> SessionModel:
        """Record a login session in the DB for audit purposes.

        Called after the HMAC auth.login() succeeds.  The token_hash
        stored here allows admin tools to see and revoke persistent
        sessions, even though the actual cookie validation is HMAC-based.
        """
        now = _utc_now()
        token_hash = hash_token(token)

        auth_session = SessionModel(
            id=str(uuid.uuid4()),
            user_id=user_id,
            workspace_id=workspace_id,
            token_hash=token_hash,
            csrf_hash=csrf_hash,
            expires_at=now + SESSION_DURATION,
        )
        self.session.add(auth_session)
        await self.session.flush()
        return auth_session

    async def get_active_sessions_for_user(
        self,
        user_id: str,
    ) -> list[SessionModel]:
        """Get all non-revoked, non-expired sessions for a user."""
        now = _utc_now()
        result = await self.session.scalars(
            select(SessionModel).where(
                SessionModel.user_id == user_id,
                SessionModel.revoked_at.is_(None),
                SessionModel.expires_at > now,
            ).order_by(SessionModel.created_at.desc())
        )
        return list(result)

    async def get_all_active_sessions(self) -> list[SessionModel]:
        """Get all active sessions across all users (admin only)."""
        now = _utc_now()
        result = await self.session.scalars(
            select(SessionModel).where(
                SessionModel.revoked_at.is_(None),
                SessionModel.expires_at > now,
            ).order_by(SessionModel.created_at.desc()).limit(500)
        )
        return list(result)

    async def revoke_session(self, session_id: str) -> None:
        """Revoke a specific DB session."""
        now = _utc_now()
        await self.session.execute(
            update(SessionModel)
            .where(SessionModel.id == session_id)
            .values(revoked_at=now)
        )
        await self.session.flush()

    async def revoke_user_sessions(self, user_id: str) -> int:
        """Revoke all active sessions for a user.

        Returns the number of revoked sessions.
        Also bumps authorization_version to invalidate HMAC sessions.
        """
        now = _utc_now()
        result = await self.session.execute(
            update(SessionModel)
            .where(
                SessionModel.user_id == user_id,
                SessionModel.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )

        # Bump authorization_version to invalidate HMAC sessions too
        await self.session.execute(
            update(UserModel)
            .where(UserModel.id == user_id)
            .values(authorization_version=UserModel.authorization_version + 1)
        )

        await self.session.flush()
        return result.rowcount

    async def build_principal(
        self,
        user: UserModel,
    ) -> AuthenticatedPrincipal:
        """Build an AuthenticatedPrincipal from a DB user.

        Integrates with the existing RBAC service to load
        roles, permissions, and workspace memberships.
        """
        return await self.rbac_service.principal_for_user_id(
            self.session, user.id
        )

    async def get_session_info(
        self,
        user: UserModel,
    ) -> dict:
        """Get session-related info for the /me endpoint."""
        roles = await self.rbac_service.roles_for(self.session, user.id)

        workspace_rows = await self.session.scalars(
            select(WorkspaceMemberModel.workspace_id).where(
                WorkspaceMemberModel.user_id == user.id,
                WorkspaceMemberModel.status == "active",
            )
        )
        workspaces = list(workspace_rows)

        return {
            "user_id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "roles": list(roles),
            "workspaces": workspaces,
        }


def _utc_now() -> datetime:
    """Return current UTC time without tzinfo (MySQL naive datetime)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
