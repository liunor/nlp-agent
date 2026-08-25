"""User management service.

Integrates with existing RBAC infrastructure for role management
and uses the shared MySQL async session factory.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerificationError, VerifyMismatchError
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from server.infrastructure.mysql.models import (
    RoleModel,
    SessionModel,
    UserModel,
    UserRoleModel,
    WorkspaceModel,
    WorkspaceMemberModel,
    OutboxMessageModel,
)

from .schemas import UserCreate, UserUpdate


class UserServiceError(Exception):
    """Base error for user service operations."""


class UserNotFoundError(UserServiceError):
    """Raised when a user is not found."""


class UserAlreadyExistsError(UserServiceError):
    """Raised when attempting to create a duplicate user."""


class SelfDeleteForbiddenError(UserServiceError):
    """Raised when an actor attempts to delete their own account."""


DEFAULT_USER_ROLE = "guest"


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
                UserModel.username_lower == data.username.casefold()
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
        # Flush user + workspace first so the owner-membership FK has its parent
        # rows present before the child row is inserted. SQLAlchemy does not
        # always topologically order these without a relationship under the
        # async driver (aiomysql), so we force the order explicitly.
        await self.session.flush([user, workspace])

        # Add user as workspace owner
        member = WorkspaceMemberModel(
            workspace_id=workspace.id,
            user_id=user.id,
            member_type="owner",
            status="active",
        )
        self.session.add(member)

        # Every account has an explicit least-privilege RBAC identity from its
        # first transaction.  The role catalog is seeded by the RBAC migration;
        # failing closed here prevents an account that can authenticate but has
        # no authorization semantics.
        guest_role = await self.session.scalar(
            select(RoleModel).where(
                RoleModel.code == DEFAULT_USER_ROLE,
                RoleModel.status == "active",
            )
        )
        if guest_role is None:
            raise UserServiceError("default guest role is not available")
        assigned_by = None
        if actor_user_id is not None:
            assigned_by = await self.session.scalar(
                select(UserModel.id).where(UserModel.id == actor_user_id)
            )
        self.session.add(
            UserRoleModel(
                user_id=user.id,
                role_id=guest_role.id,
                assigned_by_user_id=assigned_by,
            )
        )

        await self.session.flush()
        # MySQL lacks RETURNING support, so SQLAlchemy cannot populate the
        # STORED ``Computed`` column ``username_lower`` (and the
        # ``server_default`` ``created_at``/``updated_at``) via the INSERT.
        # Refresh forces a SELECT so the returned instance is fully populated
        # and downstream Pydantic ``model_validate`` calls do not trigger an
        # async lazy-load that would raise ``MissingGreenlet``.
        await self.session.refresh(user)
        return user

    async def get_user(self, user_id: str) -> UserModel:
        """Get active (non-soft-deleted) user by ID."""
        user = await self.session.scalar(
            select(UserModel).where(
                UserModel.id == user_id,
                UserModel.deleted_at.is_(None),
            )
        )
        if user is None:
            raise UserNotFoundError(f"User {user_id} not found")
        return user

    async def get_user_by_username(self, username: str) -> Optional[UserModel]:
        """Get active user by username."""
        return await self.session.scalar(
            select(UserModel).where(
                UserModel.username_lower == username.casefold(),
                UserModel.deleted_at.is_(None),
            )
        )

    async def list_users(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        status: Optional[str] = None,
        keyword: Optional[str] = None,
        include_deleted: bool = False,
    ) -> tuple[list[UserModel], int]:
        """List users with an explicit soft-delete filter for administrators."""
        query = select(UserModel)
        count_query = select(func.count()).select_from(UserModel)

        if not include_deleted:
            query = query.where(UserModel.deleted_at.is_(None))
            count_query = count_query.where(UserModel.deleted_at.is_(None))
        elif status == "deleted":
            query = query.where(UserModel.deleted_at.is_not(None))
            count_query = count_query.where(UserModel.deleted_at.is_not(None))
            status = None

        if status:
            query = query.where(UserModel.status == status)
            count_query = count_query.where(UserModel.status == status)

        if keyword:
            escaped = (
                keyword.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            pattern = f"%{escaped}%"
            like_filter = or_(
                UserModel.username.ilike(pattern, escape="\\"),
                UserModel.display_name.ilike(pattern, escape="\\"),
            )
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

    async def _revoke_user_sessions(self, user_id: str) -> None:
        """Revoke all active database sessions for a user."""
        await self.session.execute(
            update(SessionModel)
            .where(
                SessionModel.user_id == user_id,
                SessionModel.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(timezone.utc).replace(tzinfo=None))
        )
        # A disabled account, password reset, or administrator session revoke
        # must also fence its Phase-0 sandbox leases in this same transaction.
        # No runtime exists yet, but the future manager consumes this durable
        # state rather than trusting a browser logout notification.
        from server.sandbox.service import sandbox_lifecycle_service

        await sandbox_lifecycle_service.revoke_user_leases(
            self.session,
            user_id=user_id,
            reason="authorization.session_revoked",
        )

    async def _mark_authorization_changed(self, user_id: str, reason: str) -> None:
        """Persist cross-process invalidation in the same transaction."""
        from server.sandbox.service import sandbox_lifecycle_service

        await sandbox_lifecycle_service.revoke_user_leases(
            self.session,
            user_id=user_id,
            reason=f"authorization.changed:{reason}",
        )
        self.session.add(
            OutboxMessageModel(
                id=str(uuid.uuid4()),
                topic="authorization.changed",
                payload_json={"user_id": user_id, "reason": reason},
            )
        )

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

        # P1-2 / 阶段5：禁用或锁定账号时同步撤销其全部会话
        if status in ("disabled", "locked"):
            await self._revoke_user_sessions(user_id)

        await self._mark_authorization_changed(user_id, "user_status_changed")
        await self.session.flush()
        return user

    async def verify_password(self, user: UserModel, password: str) -> bool:
        """Verify a password against the stored hash."""
        try:
            return self.hasher.verify(user.password_hash, password)
        except (VerifyMismatchError, VerificationError, ValueError, TypeError):
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
        await self._revoke_user_sessions(user_id)
        await self._mark_authorization_changed(user_id, "password_changed")
        await self.session.flush()
        return user

    async def restore_user(self, user_id: str, *, actor_user_id: str) -> UserModel:
        """Restore a soft-deleted account without reviving old sessions."""
        user = await self.session.scalar(
            select(UserModel).where(UserModel.id == user_id).with_for_update()
        )
        if user is None or user.deleted_at is None:
            raise UserNotFoundError(f"Deleted user {user_id} not found")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        user.deleted_at = None
        user.status = "active"
        user.authorization_version += 1
        user.updated_at = now
        await self._revoke_user_sessions(user_id)
        await self._mark_authorization_changed(user_id, "user_restored")
        await self.session.flush()
        return user

    async def soft_delete_user(
        self,
        user_id: str,
        *,
        actor_user_id: str,
    ) -> UserModel:
        """Soft-delete a user account (admin operation).

        Preserves learning history and related records while making the account
        invisible to normal queries. Uses ``user_id`` comparison for the
        self-delete guard (not username) per review P1-2.
        """
        if user_id == actor_user_id:
            raise SelfDeleteForbiddenError("Admin cannot delete their own account")

        user = await self.get_user(user_id)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        user.status = "disabled"
        user.deleted_at = now
        user.updated_at = now
        user.authorization_version += 1

        await self._revoke_user_sessions(user_id)
        await self._mark_authorization_changed(user_id, "user_soft_deleted")
        await self.session.flush()
        return user

    async def revoke_user_sessions(
        self,
        user_id: str,
        *,
        actor_user_id: Optional[str] = None,
    ) -> int:
        """Admin revoke all active sessions for a single target user (P1-3).

        The revocation is scoped strictly by ``user_id`` — a caller can only
        ever revoke the explicitly targeted account's sessions and never
        another user's, which is the precise guard the review's P1-3 fix
        requires (``WHERE user_id = :target_user_id AND revoked_at IS NULL``).

        Bumps ``authorization_version`` so any cached tokens are invalidated
        in addition to the row-level ``revoked_at``. Returns the number of
        sessions revoked.
        """
        # Ensures the target exists; raises UserNotFoundError otherwise.
        user = await self.get_user(user_id)
        # Load the active sessions as ORM objects and revoke them in-place.
        # This keeps the identity map consistent (so any caller that later
        # re-reads a session via ``session.get`` sees ``revoked_at``) AND
        # gives an exact count, avoiding both aiomysql's unreliable UPDATE
        # rowcount and the stale-object problem of a bulk UPDATE.
        active_sessions = (
            await self.session.scalars(
                select(SessionModel).where(
                    SessionModel.user_id == user_id,
                    SessionModel.revoked_at.is_(None),
                )
            )
        ).all()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for sess in active_sessions:
            sess.revoked_at = now
        user.authorization_version += 1
        user.updated_at = now
        await self._mark_authorization_changed(user_id, "user_sessions_revoked")
        await self.session.flush()
        return len(active_sessions)

    async def update_last_login(self, user_id: str) -> None:
        """Update the last login timestamp.
        """
        await self.session.execute(
            update(UserModel)
            .where(UserModel.id == user_id, UserModel.deleted_at.is_(None))
            .values(last_login_at=datetime.now(timezone.utc).replace(tzinfo=None))
        )
