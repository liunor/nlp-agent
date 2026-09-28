"""Phase 2 warm-pool state machine and database-backed claims."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.infrastructure.mysql.models import (
    RoleModel,
    SessionModel,
    SandboxEnvironmentModel,
    SandboxLeaseModel,
    SandboxRuntimeInstanceModel,
    UserModel,
    UserRoleModel,
)

from .contracts import SandboxScope
from .optimization import should_defer_claim


class RuntimeState(StrEnum):
    READY_UNBOUND = "ready_unbound"
    CLAIMING = "claiming"
    ASSIGNED = "assigned"
    DRAINING = "draining"
    DESTROYED = "destroyed"
    FAILED = "failed"


@dataclass(frozen=True)
class RuntimeReconcilePlan:
    missing_database_ids: set[str]
    orphan_docker_ids: set[str]


@dataclass(frozen=True)
class RuntimeClaim:
    """A DB claim plus its one-time secret, which is never persisted in plaintext."""

    runtime: SandboxRuntimeInstanceModel
    nonce: str | None


_TRANSITIONS = {
    RuntimeState.READY_UNBOUND: {RuntimeState.CLAIMING, RuntimeState.FAILED, RuntimeState.DESTROYED},
    RuntimeState.CLAIMING: {RuntimeState.ASSIGNED, RuntimeState.FAILED, RuntimeState.DESTROYED},
    RuntimeState.ASSIGNED: {RuntimeState.DRAINING, RuntimeState.FAILED},
    RuntimeState.DRAINING: {RuntimeState.DESTROYED, RuntimeState.FAILED},
}


def transition_runtime(current: RuntimeState, target: RuntimeState) -> RuntimeState:
    if target not in _TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid sandbox runtime transition {current} -> {target}")
    return target


def claim_nonce_hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()


def validate_claim_nonce(stored_hash: str, supplied_nonce: str) -> bool:
    return hmac.compare_digest(stored_hash, claim_nonce_hash(supplied_nonce))


def runtime_container_name(runtime_id: str) -> str:
    """Stable opaque name: useful for recovery, but never carries a user id or nonce."""
    return f"nova-runtime-{runtime_id.replace('-', '')}"


def reconcile_runtime_ids(*, database_ids: set[str], docker_ids: set[str], now: object) -> RuntimeReconcilePlan:
    """Pure reconciliation decision; I/O and state changes stay in the Manager."""
    del now
    return RuntimeReconcilePlan(
        missing_database_ids=database_ids - docker_ids,
        orphan_docker_ids=docker_ids - database_ids,
    )


class WarmPoolService:
    """Claims only pristine READY_UNBOUND rows; used rows must be destroyed."""

    @staticmethod
    def validate_nonce(stored_hash: str, supplied_nonce: str) -> bool:
        return validate_claim_nonce(stored_hash, supplied_nonce)

    async def _has_higher_priority_waiter(
        self,
        session: AsyncSession,
        *,
        current_lease_id: str,
        current_user_id: str,
    ) -> bool:
        now = datetime.now(UTC).replace(tzinfo=None)
        current_roles = set(
            (
                await session.scalars(
                    select(RoleModel.code)
                    .join(UserRoleModel, UserRoleModel.role_id == RoleModel.id)
                    .where(
                        UserRoleModel.user_id == current_user_id,
                        RoleModel.status == "active",
                        or_(
                            UserRoleModel.expires_at.is_(None),
                            UserRoleModel.expires_at > now,
                        ),
                    )
                )
            ).all()
        )
        waiting_user_ids = list(
            (
                await session.scalars(
                    select(SandboxLeaseModel.user_id)
                    .join(SessionModel, SessionModel.id == SandboxLeaseModel.auth_session_id)
                    .join(UserModel, UserModel.id == SandboxLeaseModel.user_id)
                    .where(
                        SandboxLeaseModel.id != current_lease_id,
                        SandboxLeaseModel.state == "active",
                        SandboxLeaseModel.runtime_instance_id.is_(None),
                        SandboxLeaseModel.expires_at > now,
                        SessionModel.revoked_at.is_(None),
                        SessionModel.expires_at > now,
                        SessionModel.authorization_version == UserModel.authorization_version,
                        UserModel.status == "active",
                        UserModel.deleted_at.is_(None),
                    )
                    .order_by(SandboxLeaseModel.created_at.asc(), SandboxLeaseModel.id.asc())
                )
            ).all()
        )
        if not waiting_user_ids:
            return False
        role_rows = (
            await session.execute(
                select(UserRoleModel.user_id, RoleModel.code)
                .join(RoleModel, RoleModel.id == UserRoleModel.role_id)
                .where(
                    UserRoleModel.user_id.in_(waiting_user_ids),
                    RoleModel.status == "active",
                    or_(
                        UserRoleModel.expires_at.is_(None),
                        UserRoleModel.expires_at > now,
                    ),
                )
            )
        ).all()
        roles_by_user: dict[str, set[str]] = {}
        for user_id, role_code in role_rows:
            roles_by_user.setdefault(str(user_id), set()).add(str(role_code))
        waiting_roles = (
            roles_by_user.get(str(user_id), {"guest"}) for user_id in waiting_user_ids
        )
        return should_defer_claim(
            current_role_codes=current_roles or {"guest"},
            waiting_role_codes=waiting_roles,
        )

    async def claim(
        self,
        session: AsyncSession,
        scope: SandboxScope,
        *,
        lease_id: str | None = None,
    ) -> RuntimeClaim | None:
        environment = await session.scalar(
            select(SandboxEnvironmentModel)
            .where(SandboxEnvironmentModel.owner_user_id == scope.owner_user_id)
            .with_for_update()
        )
        if environment is None:
            raise LookupError("sandbox environment does not exist")
        existing = None
        if environment.active_runtime_id is not None:
            existing = await session.scalar(
                select(SandboxRuntimeInstanceModel)
                .where(
                    SandboxRuntimeInstanceModel.id == environment.active_runtime_id,
                    SandboxRuntimeInstanceModel.environment_id == environment.id,
                    SandboxRuntimeInstanceModel.state == RuntimeState.ASSIGNED,
                )
                .with_for_update()
            )
        if existing is not None:
            # A claim is one-shot.  Rotate the nonce when a second command
            # reuses the same assigned runtime so callers never fall back to a
            # stale ticket or a plaintext secret.
            nonce = str(uuid4())
            existing.claim_nonce_hash = claim_nonce_hash(nonce)
            return RuntimeClaim(runtime=existing, nonce=nonce)
        if lease_id is not None and await self._has_higher_priority_waiter(
            session,
            current_lease_id=lease_id,
            current_user_id=scope.owner_user_id,
        ):
            return None
        runtime = await session.scalar(
            select(SandboxRuntimeInstanceModel)
            .where(
                SandboxRuntimeInstanceModel.state == RuntimeState.READY_UNBOUND,
                SandboxRuntimeInstanceModel.environment_id.is_(None),
                SandboxRuntimeInstanceModel.resource_profile_id == environment.resource_profile_id,
            )
            .order_by(SandboxRuntimeInstanceModel.created_at.asc(), SandboxRuntimeInstanceModel.id.asc())
            .with_for_update(skip_locked=True)
        )
        if runtime is None:
            return None
        runtime.state = RuntimeState.CLAIMING
        runtime.environment_id = environment.id
        runtime.generation = environment.generation
        nonce = str(uuid4())
        runtime.claim_nonce_hash = claim_nonce_hash(nonce)
        runtime.state = transition_runtime(RuntimeState.CLAIMING, RuntimeState.ASSIGNED)
        environment.active_runtime_id = runtime.id
        return RuntimeClaim(runtime=runtime, nonce=nonce)

    async def mark_draining(self, session: AsyncSession, runtime_id: str) -> SandboxRuntimeInstanceModel | None:
        runtime = await session.scalar(select(SandboxRuntimeInstanceModel).where(SandboxRuntimeInstanceModel.id == runtime_id).with_for_update())
        if runtime is None or runtime.state != RuntimeState.ASSIGNED:
            return None
        runtime.state = transition_runtime(RuntimeState.ASSIGNED, RuntimeState.DRAINING)
        return runtime

    async def mark_destroyed(self, session: AsyncSession, runtime_id: str) -> None:
        runtime = await session.scalar(select(SandboxRuntimeInstanceModel).where(SandboxRuntimeInstanceModel.id == runtime_id).with_for_update())
        if runtime is None:
            return
        if runtime.state == RuntimeState.DRAINING:
            runtime.state = transition_runtime(RuntimeState.DRAINING, RuntimeState.DESTROYED)
        elif runtime.state != RuntimeState.DESTROYED:
            runtime.state = RuntimeState.FAILED
        runtime.claim_nonce_hash = None
        if runtime.environment_id is not None:
            environment = await session.get(SandboxEnvironmentModel, runtime.environment_id, with_for_update=True)
            if environment is not None and environment.active_runtime_id == runtime.id:
                environment.active_runtime_id = None


warm_pool_service = WarmPoolService()
