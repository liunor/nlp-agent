"""Phase 0 persistence for sandbox ownership and session leases."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.infrastructure.mysql.models import (
    OutboxMessageModel,
    SandboxEnvironmentModel,
    SandboxLeaseModel,
    SessionModel,
    UserModel,
)

from .contracts import SandboxScope


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SandboxLifecycleService:
    """Own sandbox leases without creating or contacting a runtime.

    Every command receives a server-derived ``SandboxScope``.  No public
    method accepts a browser-supplied owner id, runtime id, or generation.
    """

    async def describe(self, session: AsyncSession, scope: SandboxScope) -> dict:
        await self.revoke_expired_leases(session)
        environment = await session.scalar(
            select(SandboxEnvironmentModel).where(
                SandboxEnvironmentModel.owner_user_id == scope.owner_user_id
            )
        )
        lease = None
        if environment is not None:
            lease = await session.scalar(
                select(SandboxLeaseModel).where(
                    SandboxLeaseModel.environment_id == environment.id,
                    SandboxLeaseModel.auth_session_id == scope.auth_session_id,
                )
            )
        return self._payload(environment, lease)

    async def ensure_environment(
        self,
        session: AsyncSession,
        scope: SandboxScope,
        *,
        resource_profile_id: str = "python-base",
    ) -> SandboxEnvironmentModel:
        """Get-or-create the owner's logical environment without creating a lease.

        Scratch execution can start before the Interactive Kernel is opened.
        The owner uniqueness constraint is the concurrency authority, so a
        nested transaction absorbs the loser of two first-creation requests.
        """
        # Serialize first-creation and lease renewal for one owner.  Without
        # this stable row lock, concurrent MySQL transactions can deadlock in
        # the broad auth-expiry update before the owner uniqueness constraint
        # gets a chance to resolve the race.
        await session.get(UserModel, scope.owner_user_id, with_for_update=True)
        # Avoid an absent-row gap lock; MySQL can deadlock two first creators.
        environment = await session.scalar(
            select(SandboxEnvironmentModel).where(
                SandboxEnvironmentModel.owner_user_id == scope.owner_user_id
            )
        )
        if environment is not None:
            environment = await session.scalar(
                select(SandboxEnvironmentModel)
                .where(SandboxEnvironmentModel.owner_user_id == scope.owner_user_id)
                .with_for_update()
            )
            return environment
        try:
            async with session.begin_nested():
                environment = SandboxEnvironmentModel(
                    id=str(uuid4()),
                    owner_user_id=scope.owner_user_id,
                    resource_profile_id=resource_profile_id,
                    profile_revision=1,
                    status="ready",
                    generation=scope.generation,
                    last_active_at=_utc_now(),
                )
                session.add(environment)
                await session.flush()
        except IntegrityError:
            environment = await session.scalar(
                select(SandboxEnvironmentModel)
                .where(SandboxEnvironmentModel.owner_user_id == scope.owner_user_id)
                .with_for_update()
            )
            if environment is None:
                raise
        assert environment is not None
        return environment

    async def ensure_current_lease(self, session: AsyncSession, scope: SandboxScope) -> dict:
        environment = await self.ensure_environment(session, scope)
        # The owner row is locked by ensure_environment, so this expiry pass
        # now has a deterministic lock order across concurrent claims.
        await self.revoke_expired_leases(session)
        now = _utc_now()
        environment.generation = max(environment.generation, scope.generation)
        environment.last_active_at = now
        environment.lease_deadline_at = scope.lease_expires_at

        lease = await session.scalar(
            select(SandboxLeaseModel)
            .where(
                SandboxLeaseModel.environment_id == environment.id,
                SandboxLeaseModel.auth_session_id == scope.auth_session_id,
            )
            .with_for_update()
        )
        if lease is None:
            lease = SandboxLeaseModel(
                id=str(uuid4()),
                environment_id=environment.id,
                user_id=scope.owner_user_id,
                auth_session_id=scope.auth_session_id,
                workspace_id=scope.workspace_id,
                generation=environment.generation,
                actor_type="browser",
                state="active",
                expires_at=scope.lease_expires_at,
            )
            session.add(lease)
        else:
            lease.state = "active"
            # Lease generation is the Environment fencing generation.  The
            # authenticated authorization version remains in ``scope`` and is
            # checked separately by the Manager's auth lifecycle guard.
            lease.generation = environment.generation
            lease.workspace_id = scope.workspace_id
            lease.expires_at = scope.lease_expires_at
            lease.renewed_at = now
            lease.released_at = None
            lease.reason = None
        await session.flush()
        return self._payload(environment, lease)

    async def release_auth_session(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        user_id: str,
        auth_session_id: str,
        reason: str,
    ) -> None:
        """Release only one auth session; never destroy another session's lease."""
        async with factory.begin() as session:
            await self.release_auth_session_in_transaction(
                session,
                user_id=user_id,
                auth_session_id=auth_session_id,
                reason=reason,
            )

    async def release_auth_session_in_transaction(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        auth_session_id: str,
        reason: str,
    ) -> None:
        """Release a lease in the transaction that revoked its auth session."""
        now = _utc_now()
        result = await session.execute(
            update(SandboxLeaseModel)
            .where(
                SandboxLeaseModel.user_id == user_id,
                SandboxLeaseModel.auth_session_id == auth_session_id,
                SandboxLeaseModel.state == "active",
            )
            .values(state="released", released_at=now, reason=reason)
        )
        if result.rowcount:
            session.add(
                OutboxMessageModel(
                    id=str(uuid4()),
                    topic="sandbox.lease.released",
                    payload_json={
                        "user_id": user_id,
                        "auth_session_id": auth_session_id,
                        "reason": reason,
                    },
                )
            )

    async def revoke_user_leases(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        reason: str,
    ) -> None:
        """Revoke all active leases inside the caller's authz transaction."""
        now = _utc_now()
        result = await session.execute(
            update(SandboxLeaseModel)
            .where(SandboxLeaseModel.user_id == user_id, SandboxLeaseModel.state == "active")
            .values(state="revoked", released_at=now, reason=reason)
        )
        if result.rowcount:
            session.add(
                OutboxMessageModel(
                    id=str(uuid4()),
                    topic="sandbox.lease.revoked",
                    payload_json={"user_id": user_id, "reason": reason},
                )
            )

    async def revoke_expired_leases(
        self,
        session: AsyncSession,
        *,
        now: datetime | None = None,
    ) -> None:
        """Mark expired session leases before any request observes their state.

        The Phase 2 Manager also invokes this method from its periodic
        reconciler.  Correctness comes from the database state, not from a
        best-effort browser disconnect notification.
        """
        current_time = now or _utc_now()
        invalid_auth_session = select(SessionModel.id).join(
            UserModel, UserModel.id == SessionModel.user_id
        ).where(
            SessionModel.id == SandboxLeaseModel.auth_session_id,
            or_(
                SessionModel.revoked_at.is_not(None),
                SessionModel.expires_at <= current_time,
                SessionModel.authorization_version != UserModel.authorization_version,
                UserModel.status != "active",
                UserModel.deleted_at.is_not(None),
            ),
        ).exists()
        await session.execute(
            update(SandboxLeaseModel)
            .where(
                SandboxLeaseModel.state == "active",
                or_(SandboxLeaseModel.expires_at <= current_time, invalid_auth_session),
            )
            .values(state="expired", released_at=current_time, reason="auth.session.expired")
        )

    async def reconcile_expired_leases(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Run the durable expiry pass independently of browser activity."""
        async with factory.begin() as session:
            await self.revoke_expired_leases(session)

    @staticmethod
    def _payload(
        environment: SandboxEnvironmentModel | None,
        lease: SandboxLeaseModel | None,
    ) -> dict:
        return {
            "phase": 0,
            "runtime_available": False,
            "environment": None
            if environment is None
            else {
                "id": environment.id,
                "status": environment.status,
                "generation": environment.generation,
                "profile": environment.resource_profile_id,
            },
            "lease": None
            if lease is None
            else {
                "id": lease.id,
                "state": lease.state,
                "generation": lease.generation,
                "expires_at": lease.expires_at,
            },
        }


sandbox_lifecycle_service = SandboxLifecycleService()
