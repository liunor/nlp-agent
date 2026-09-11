"""Deterministic Sandbox Manager used only by the real HTTP test process."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select

from server.infrastructure.mysql.models import (
    SandboxLeaseModel,
    SandboxRuntimeInstanceModel,
    SessionModel,
    UserModel,
)
from server.sandbox.contracts import SandboxScope
from server.sandbox.inmemory_runtime import InMemoryRuntime
from server.sandbox.manager import auth_lifecycle_allows_execution
from server.sandbox.warm_pool import RuntimeClaim, RuntimeState


class DeterministicSandboxManager:
    """A DB-backed Manager seam without Docker or a guest runtime.

    The Web process remains in ``docker`` gateway mode, so lease tickets are
    issued and verified exactly as they are for the production Manager.  The
    claimed runtime executes through the repository's explicit in-memory
    development runtime; it never opens a Docker socket or external service.
    """

    def __init__(self) -> None:
        self._session_factory = None
        self._runtime = InMemoryRuntime()

    def bind(self, session_factory) -> None:
        self._session_factory = session_factory

    async def claim(self, scope: SandboxScope, *, lease_id: str) -> RuntimeClaim | None:
        if self._session_factory is None:
            raise RuntimeError("deterministic Sandbox Manager is not bound")
        async with self._session_factory.begin() as session:
            lease = await session.get(SandboxLeaseModel, lease_id, with_for_update=True)
            if (
                lease is None
                or str(lease.user_id) != scope.owner_user_id
                or str(lease.auth_session_id) != scope.auth_session_id
                or str(lease.workspace_id) != scope.workspace_id
                or lease.state != "active"
                or lease.expires_at <= _utc_now()
            ):
                return None
            if lease.runtime_instance_id:
                runtime = await session.get(
                    SandboxRuntimeInstanceModel, lease.runtime_instance_id, with_for_update=True
                )
                if runtime is not None and runtime.state == RuntimeState.ASSIGNED:
                    return RuntimeClaim(runtime=runtime, nonce=None)

            runtime = SandboxRuntimeInstanceModel(
                id=str(uuid4()),
                environment_id=str(lease.environment_id),
                runtime_kind="inmemory-stub",
                resource_profile_id="python-base",
                state=RuntimeState.ASSIGNED,
                generation=lease.generation,
                external_runtime_id=None,
            )
            nonce = secrets.token_urlsafe(24)
            runtime.claim_nonce_hash = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
            session.add(runtime)
            # The lease has an FK to the runtime row. Flush the parent before
            # assigning the child FK so MySQL never observes an invalid order.
            await session.flush()
            lease.runtime_instance_id = runtime.id
            await session.flush()
            return RuntimeClaim(runtime=runtime, nonce=nonce)

    async def execute_claimed(
        self,
        scope: SandboxScope,
        *,
        lease_id: str,
        runtime_id: str,
        generation: int,
        nonce: str | None,
        source: str,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> dict[str, object]:
        del trace_id, span_id
        if self._session_factory is None:
            raise RuntimeError("deterministic Sandbox Manager is not bound")
        async with self._session_factory.begin() as session:
            lease = await session.get(SandboxLeaseModel, lease_id, with_for_update=True)
            runtime = await session.get(SandboxRuntimeInstanceModel, runtime_id, with_for_update=True)
            auth_session = None if lease is None else await session.get(SessionModel, lease.auth_session_id)
            user = None if lease is None else await session.get(UserModel, lease.user_id)
            if (
                lease is None
                or runtime is None
                or str(lease.user_id) != scope.owner_user_id
                or str(lease.auth_session_id) != scope.auth_session_id
                or str(lease.workspace_id) != scope.workspace_id
                or lease.runtime_instance_id != runtime.id
                or runtime.environment_id != lease.environment_id
                or runtime.generation != generation
                or runtime.state != RuntimeState.ASSIGNED
                or not auth_lifecycle_allows_execution(
                    lease=lease,
                    auth_session=auth_session,
                    user=user,
                    scope_generation=scope.generation,
                    scope_workspace_id=scope.workspace_id,
                    now=_utc_now(),
                )
            ):
                raise PermissionError("sandbox command lease or runtime is invalid")
            expected = hashlib.sha256(nonce.encode("utf-8")).hexdigest() if nonce else None
            if not runtime.claim_nonce_hash or expected != runtime.claim_nonce_hash:
                raise PermissionError("sandbox command nonce is invalid or already used")
            runtime.claim_nonce_hash = None
        return await self._runtime.execute(user_id=scope.owner_user_id, source=source)

    async def runtime_usage(
        self,
        scope: SandboxScope,
        *,
        lease_id: str,
        runtime_id: str,
        generation: int,
    ) -> dict[str, float | None]:
        if self._session_factory is None:
            raise RuntimeError("deterministic Sandbox Manager is not bound")
        async with self._session_factory() as session:
            lease = await session.get(SandboxLeaseModel, lease_id)
            runtime = await session.get(SandboxRuntimeInstanceModel, runtime_id)
            if (
                lease is None
                or runtime is None
                or str(lease.user_id) != scope.owner_user_id
                or str(lease.auth_session_id) != scope.auth_session_id
                or str(lease.workspace_id) != scope.workspace_id
                or lease.runtime_instance_id != runtime.id
                or runtime.generation != generation
                or runtime.state != RuntimeState.ASSIGNED
                or lease.state != "active"
                or lease.expires_at <= _utc_now()
            ):
                raise PermissionError("sandbox usage is not authorized for this runtime")
        return {"cpu_percent": 0.0, "memory_percent": 0.0}

    async def reset_runtime(
        self,
        scope: SandboxScope,
        *,
        lease_id: str,
        runtime_id: str,
        generation: int | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        del trace_id, span_id
        if self._session_factory is None:
            raise RuntimeError("deterministic Sandbox Manager is not bound")
        async with self._session_factory.begin() as session:
            lease = await session.get(SandboxLeaseModel, lease_id, with_for_update=True)
            runtime = await session.get(SandboxRuntimeInstanceModel, runtime_id, with_for_update=True)
            if (
                lease is None
                or runtime is None
                or str(lease.user_id) != scope.owner_user_id
                or str(lease.auth_session_id) != scope.auth_session_id
                or str(lease.workspace_id) != scope.workspace_id
                or lease.runtime_instance_id != runtime.id
                or runtime.generation != (generation if generation is not None else runtime.generation)
                or runtime.state != RuntimeState.ASSIGNED
            ):
                raise PermissionError("sandbox reset is not authorized for this runtime")
            runtime.state = RuntimeState.DESTROYED
            lease.runtime_instance_id = None
        await self._runtime.restart(user_id=scope.owner_user_id)

    async def capacity_snapshot(self) -> dict[str, int | str]:
        if self._session_factory is None:
            raise RuntimeError("deterministic Sandbox Manager is not bound")
        async with self._session_factory() as session:
            ready = await session.scalar(
                select(func.count()).select_from(SandboxRuntimeInstanceModel).where(
                    SandboxRuntimeInstanceModel.state == RuntimeState.READY_UNBOUND
                )
            )
            creating = await session.scalar(
                select(func.count()).select_from(SandboxRuntimeInstanceModel).where(
                    SandboxRuntimeInstanceModel.state == "creating"
                )
            )
        return {"resource_profile": "python-base", "ready": int(ready or 0), "creating": int(creating or 0), "target": 0, "deficit": 0, "adaptive_target": 0}

    async def close(self) -> None:
        return None


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
