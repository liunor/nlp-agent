"""Phase 2 warm-pool state machine and database-backed claims."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.infrastructure.mysql.models import SandboxEnvironmentModel, SandboxRuntimeInstanceModel

from .contracts import SandboxScope


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

    async def claim(self, session: AsyncSession, scope: SandboxScope) -> RuntimeClaim | None:
        environment = await session.scalar(
            select(SandboxEnvironmentModel)
            .where(SandboxEnvironmentModel.owner_user_id == scope.owner_user_id)
            .with_for_update()
        )
        if environment is None:
            raise LookupError("sandbox environment does not exist")
        existing = await session.scalar(
            select(SandboxRuntimeInstanceModel)
            .where(SandboxRuntimeInstanceModel.environment_id == environment.id, SandboxRuntimeInstanceModel.state == RuntimeState.ASSIGNED)
            .with_for_update()
        )
        if existing is not None:
            return RuntimeClaim(runtime=existing, nonce=None)
        runtime = await session.scalar(
            select(SandboxRuntimeInstanceModel)
            .where(
                SandboxRuntimeInstanceModel.state == RuntimeState.READY_UNBOUND,
                SandboxRuntimeInstanceModel.environment_id.is_(None),
                SandboxRuntimeInstanceModel.resource_profile_id == environment.resource_profile_id,
            )
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
