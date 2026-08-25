"""Phase 2 Sandbox Manager: the only application component allowed to invoke Docker.

It deliberately separates short database transactions from slower Docker I/O.
The HTTP application uses leases; a separately deployed Manager owns this class.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import exists, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.infrastructure.mysql.models import SandboxEnvironmentModel, SandboxLeaseModel, SandboxRuntimeInstanceModel

from .contracts import SandboxScope
from .docker_runtime import DockerRuntimeAdapter
from .warm_pool import RuntimeClaim, RuntimeState, reconcile_runtime_ids, runtime_container_name, warm_pool_service


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def refill_deficit(*, target: int, ready_count: int, creating_count: int) -> int:
    """Count work needed without treating assigned/dirty instances as reusable capacity."""
    return max(0, target - ready_count - creating_count)


@dataclass(frozen=True)
class ReconcileActions:
    mark_missing_failed: set[str]
    destroy_orphans: set[str]


def reconcile_actions(*, database_ids: set[str], docker_ids: set[str]) -> ReconcileActions:
    plan = reconcile_runtime_ids(database_ids=database_ids, docker_ids=docker_ids, now=_utc_now())
    return ReconcileActions(
        mark_missing_failed=plan.missing_database_ids,
        destroy_orphans=plan.orphan_docker_ids,
    )


class WarmPoolManager:
    """Create clean slots, lease them atomically, then destroy and replenish them.

    The manager never "cleans" an assigned container for reuse.  A destroy failure
    leaves the row in FAILED and reconciliation keeps retrying Docker cleanup.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        docker: DockerRuntimeAdapter,
        resource_profile_id: str,
        ready_target: int,
    ) -> None:
        self._session_factory = session_factory
        self._docker = docker
        self._resource_profile_id = resource_profile_id
        self._ready_target = ready_target

    async def refill(self) -> int:
        """Create only the deficit. Docker runs outside every database transaction."""
        lock_name = f"nova.sandbox.pool.{self._resource_profile_id}"
        async with self._session_factory() as lock_session:
            acquired = await lock_session.scalar(text("SELECT GET_LOCK(:name, 0)"), {"name": lock_name})
            if not acquired:
                return 0
            try:
                await self._fail_stale_creating()
                counts = await self._counts(lock_session)
                deficit = refill_deficit(
                    target=self._ready_target,
                    ready_count=counts.ready_count,
                    creating_count=counts.creating_count,
                )
                for _ in range(deficit):
                    await self._create_one()
                return deficit
            finally:
                await lock_session.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})

    async def claim(self, scope: SandboxScope, *, lease_id: str) -> RuntimeClaim | None:
        claim = await self._claim_once(scope, lease_id=lease_id)
        if claim is None:
            await self.refill()
            claim = await self._claim_once(scope, lease_id=lease_id)
        if claim is None:
            return None
        external_id = claim.runtime.external_runtime_id
        if external_id and await self._docker.kernel_ready(external_id):
            return claim
        await self.destroy_runtime(claim.runtime.id, reason="kernel.not.ready")
        return None

    async def _claim_once(self, scope: SandboxScope, *, lease_id: str) -> RuntimeClaim | None:
        async with self._session_factory.begin() as session:
            lease = await session.get(SandboxLeaseModel, lease_id, with_for_update=True)
            if (
                lease is None
                or lease.user_id != scope.owner_user_id
                or lease.auth_session_id != scope.auth_session_id
                or lease.state != "active"
                or lease.generation != scope.generation
                or lease.expires_at <= _utc_now()
            ):
                return None
            claim = await warm_pool_service.claim(session, scope)
            if claim is not None:
                lease.runtime_instance_id = claim.runtime.id
            return claim

    async def destroy_runtime(self, runtime_id: str, *, reason: str) -> None:
        async with self._session_factory.begin() as session:
            runtime = await warm_pool_service.mark_draining(session, runtime_id)
            if runtime is None:
                return
            external_id = runtime.external_runtime_id
        if external_id:
            try:
                await self._docker.destroy(external_id)
            except Exception as error:
                async with self._session_factory.begin() as session:
                    runtime = await session.get(SandboxRuntimeInstanceModel, runtime_id, with_for_update=True)
                    if runtime is not None:
                        runtime.failure_reason = f"destroy.retry: {error}"[:500]
                return
        async with self._session_factory.begin() as session:
            runtime = await session.scalar(
                select(SandboxRuntimeInstanceModel)
                .where(SandboxRuntimeInstanceModel.id == runtime_id)
                .with_for_update()
            )
            if runtime is not None:
                runtime.failure_reason = reason
            await warm_pool_service.mark_destroyed(session, runtime_id)
        await self.refill()

    async def reset_runtime(self, runtime_id: str) -> None:
        """Fence every existing ticket before destroying the user's runtime."""
        async with self._session_factory.begin() as session:
            runtime = await session.get(SandboxRuntimeInstanceModel, runtime_id, with_for_update=True)
            if runtime is None or runtime.environment_id is None:
                raise LookupError("sandbox runtime does not exist")
            environment = await session.get(SandboxEnvironmentModel, runtime.environment_id, with_for_update=True)
            if environment is not None:
                environment.generation += 1
        await self.destroy_runtime(runtime_id, reason="user.restart")

    async def reconcile(self) -> ReconcileActions:
        """Fail DB rows for vanished containers and remove unmanaged-in-DB orphans."""
        async with self._session_factory() as session:
            rows = list(
                (await session.scalars(
                    select(SandboxRuntimeInstanceModel).where(
                        SandboxRuntimeInstanceModel.external_runtime_id.is_not(None),
                        SandboxRuntimeInstanceModel.state.in_(
                            (RuntimeState.READY_UNBOUND, RuntimeState.ASSIGNED, RuntimeState.DRAINING)
                        ),
                    )
                )).all()
            )
        database_by_external_id = {row.external_runtime_id: row.id for row in rows if row.external_runtime_id}
        actions = reconcile_actions(
            database_ids=set(database_by_external_id), docker_ids=await self._docker.managed_runtime_ids()
        )
        if actions.mark_missing_failed:
            async with self._session_factory.begin() as session:
                for external_id in actions.mark_missing_failed:
                    runtime = await session.scalar(
                        select(SandboxRuntimeInstanceModel)
                        .where(SandboxRuntimeInstanceModel.id == database_by_external_id[external_id])
                        .with_for_update()
                    )
                    if runtime is not None and runtime.state != RuntimeState.DESTROYED:
                        runtime.state = RuntimeState.FAILED
                        runtime.failure_reason = "docker.container.missing"
                        runtime.claim_nonce_hash = None
        for external_id in actions.destroy_orphans:
            try:
                await self._docker.destroy(external_id)
            except Exception:
                # It remains labelled and will be discovered again on the next pass.
                pass
        await self._retry_draining_runtimes()
        await self._destroy_unleased_assigned_runtimes()
        await self.refill()
        return actions

    async def _destroy_unleased_assigned_runtimes(self) -> None:
        """Lease expiry is authoritative even when an Outbox message was missed."""
        now = _utc_now()
        active_lease = exists(
            select(SandboxLeaseModel.id).where(
                SandboxLeaseModel.environment_id == SandboxRuntimeInstanceModel.environment_id,
                SandboxLeaseModel.state == "active",
                SandboxLeaseModel.expires_at > now,
            )
        )
        async with self._session_factory() as session:
            runtime_ids = list(
                (await session.scalars(
                    select(SandboxRuntimeInstanceModel.id).where(
                        SandboxRuntimeInstanceModel.state == RuntimeState.ASSIGNED,
                        ~active_lease,
                    )
                )).all()
            )
        for runtime_id in runtime_ids:
            await self.destroy_runtime(runtime_id, reason="lease.not.active")

    async def execute_claimed(
        self,
        scope: SandboxScope,
        *,
        lease_id: str,
        runtime_id: str,
        generation: int,
        nonce: str | None,
        source: str,
    ) -> dict[str, object]:
        """Fence a one-time browser command before it ever reaches Docker."""
        async with self._session_factory.begin() as session:
            lease = await session.get(SandboxLeaseModel, lease_id, with_for_update=True)
            runtime = await session.get(SandboxRuntimeInstanceModel, runtime_id, with_for_update=True)
            if (
                lease is None or runtime is None or lease.user_id != scope.owner_user_id
                or lease.auth_session_id != scope.auth_session_id or lease.state != "active"
                or lease.generation != generation or runtime.generation != generation
                or runtime.state != RuntimeState.ASSIGNED
                or runtime.environment_id != lease.environment_id
            ):
                raise PermissionError("sandbox command lease, generation, or nonce is invalid")
            if runtime.claim_nonce_hash is not None:
                if nonce is None or not warm_pool_service.validate_nonce(runtime.claim_nonce_hash, nonce):
                    raise PermissionError("sandbox command lease, generation, or nonce is invalid")
                runtime.claim_nonce_hash = None
            external_id = runtime.external_runtime_id
        if not external_id:
            raise RuntimeError("claimed runtime has no container id")
        try:
            return await self._docker.execute(external_id, source=source)
        except TimeoutError:
            # An interrupted kernel can retain a corrupt execution state; replace
            # it rather than letting a later command inherit an unknown process.
            await self.destroy_runtime(runtime_id, reason="execution.timeout")
            raise

    async def _retry_draining_runtimes(self) -> None:
        async with self._session_factory() as session:
            runtime_ids = list(
                (await session.scalars(
                    select(SandboxRuntimeInstanceModel.id).where(
                        SandboxRuntimeInstanceModel.state == RuntimeState.DRAINING,
                        SandboxRuntimeInstanceModel.external_runtime_id.is_not(None),
                    )
                )).all()
            )
        for runtime_id in runtime_ids:
            async with self._session_factory() as session:
                runtime = await session.get(SandboxRuntimeInstanceModel, runtime_id)
                external_id = None if runtime is None else runtime.external_runtime_id
            if not external_id:
                continue
            try:
                await self._docker.destroy(external_id)
            except Exception:
                continue
            async with self._session_factory.begin() as session:
                await warm_pool_service.mark_destroyed(session, runtime_id)

    async def _fail_stale_creating(self) -> None:
        deadline = _utc_now() - timedelta(minutes=5)
        async with self._session_factory.begin() as session:
            rows = list(
                (await session.scalars(
                    select(SandboxRuntimeInstanceModel)
                    .where(
                        SandboxRuntimeInstanceModel.resource_profile_id == self._resource_profile_id,
                        SandboxRuntimeInstanceModel.state == "creating",
                        SandboxRuntimeInstanceModel.created_at < deadline,
                    )
                    .with_for_update()
                )).all()
            )
            for runtime in rows:
                runtime.state = RuntimeState.FAILED
                runtime.failure_reason = "pool.create.timeout"

    async def _create_one(self) -> None:
        runtime_id = str(uuid4())
        async with self._session_factory.begin() as session:
            session.add(
                SandboxRuntimeInstanceModel(
                    id=runtime_id,
                    runtime_kind="docker",
                    image_digest=self._docker.config.image,
                    resource_profile_id=self._resource_profile_id,
                    state="creating",
                )
            )
        try:
            name = runtime_container_name(runtime_id)
            if await self._docker.image_cached():
                external_id = await self._docker.create_l1(name=name)
                async with self._session_factory.begin() as session:
                    runtime = await session.get(SandboxRuntimeInstanceModel, runtime_id, with_for_update=True)
                    if runtime is not None:
                        runtime.external_runtime_id = external_id
                        runtime.state = "created"
                await self._docker.start_l1(external_id)
            else:
                # L0 fallback: Docker resolves the pinned digest, then this
                # slot proceeds through the same L3 readiness gate.
                external_id = await self._docker.create_ready(name=name, claim_nonce="")
            if not await self._docker.kernel_ready(external_id):
                await self._docker.destroy(external_id)
                raise RuntimeError("new sandbox kernel did not become ready")
        except Exception as error:
            async with self._session_factory.begin() as session:
                runtime = await session.get(SandboxRuntimeInstanceModel, runtime_id, with_for_update=True)
                if runtime is not None:
                    runtime.state = RuntimeState.FAILED
                    runtime.failure_reason = str(error)[:500]
            return
        async with self._session_factory.begin() as session:
            runtime = await session.get(SandboxRuntimeInstanceModel, runtime_id, with_for_update=True)
            if runtime is not None and runtime.state == "creating":
                runtime.external_runtime_id = external_id
                runtime.state = RuntimeState.READY_UNBOUND
                runtime.last_heartbeat_at = _utc_now()

    @dataclass(frozen=True)
    class _Counts:
        ready_count: int
        creating_count: int

    async def _counts(self, session: AsyncSession) -> _Counts:
        rows = list(
            (await session.scalars(
                select(SandboxRuntimeInstanceModel.state).where(
                    SandboxRuntimeInstanceModel.resource_profile_id == self._resource_profile_id,
                    SandboxRuntimeInstanceModel.state.in_((RuntimeState.READY_UNBOUND, "creating")),
                )
            )).all()
        )
        return self._Counts(
            ready_count=sum(state == RuntimeState.READY_UNBOUND for state in rows),
            creating_count=sum(state == "creating" for state in rows),
        )
