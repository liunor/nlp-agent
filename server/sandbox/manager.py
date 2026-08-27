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
from configs.settings import settings

from server.infrastructure.mysql.models import SandboxEnvironmentModel, SandboxLeaseModel, SandboxRuntimeInstanceModel, SessionModel, UserModel

from .contracts import SandboxScope
from .faults import SandboxFaultInjector
from .optimization import AdaptivePoolPolicy
from .runtime_adapters import SandboxRuntimeAdapter
from .warm_pool import RuntimeClaim, RuntimeState, reconcile_runtime_ids, runtime_container_name, warm_pool_service


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def refill_deficit(*, target: int, ready_count: int, creating_count: int) -> int:
    """Count work needed without treating assigned/dirty instances as reusable capacity."""
    return max(0, target - ready_count - creating_count)


def ready_state_after_kernel_check(current_state: str) -> str:
    """Both L0 and cached-image L1 creation paths become claimable after health."""
    return RuntimeState.READY_UNBOUND if current_state in {"creating", "created"} else current_state


def auth_lifecycle_allows_execution(
    *,
    lease: SandboxLeaseModel,
    auth_session: SessionModel | None,
    user: UserModel | None,
    scope_generation: int,
    scope_workspace_id: str | None = None,
    now: datetime,
) -> bool:
    """Keep a claimed runtime fail-closed across the auth lifecycle.

    Reconciliation eventually destroys runtimes after logout/revocation, but a
    command can arrive in the small window before that sweep.  The execution
    path therefore repeats the authoritative lease/session/user checks rather
    than trusting a previously issued ticket or a stale runtime assignment.
    """
    return bool(
        auth_session is not None
        and user is not None
        and auth_session.user_id == lease.user_id
        and auth_session.workspace_id == lease.workspace_id
        and (scope_workspace_id is None or auth_session.workspace_id == scope_workspace_id)
        and lease.expires_at > now
        and auth_session.revoked_at is None
        and auth_session.expires_at > now
        and auth_session.authorization_version == user.authorization_version
        and auth_session.authorization_version == scope_generation
        and user.status == "active"
        and user.deleted_at is None
    )


def runtime_control_allows(
    *,
    scope: SandboxScope,
    lease: SandboxLeaseModel,
    runtime: SandboxRuntimeInstanceModel,
    expected_generation: int | None = None,
    now: datetime,
) -> bool:
    """Bind reset/interrupt to the caller's current lease and runtime row."""
    return bool(
        lease.user_id == scope.owner_user_id
        and lease.auth_session_id == scope.auth_session_id
        and lease.workspace_id == scope.workspace_id
        and lease.state == "active"
        and lease.expires_at > now
        and lease.runtime_instance_id == runtime.id
        and lease.environment_id == runtime.environment_id
        and lease.generation == runtime.generation
        and (expected_generation is None or runtime.generation == expected_generation)
        and runtime.state == RuntimeState.ASSIGNED
    )


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
        docker: SandboxRuntimeAdapter,
        resource_profile_id: str,
        ready_target: int,
        adaptive_policy: AdaptivePoolPolicy | None = None,
        fault_injector: SandboxFaultInjector | None = None,
        adaptive_state_store: object | None = None,
        metrics_store: object | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._docker = docker
        self._runtime_kind = str(getattr(docker, "runtime_kind", "docker"))
        self._resource_profile_id = resource_profile_id
        self._ready_target = ready_target
        self._adaptive_policy = adaptive_policy
        self._faults = fault_injector or SandboxFaultInjector.from_env()
        self._adaptive_state_store = adaptive_state_store
        self._metrics_store = metrics_store
        self._requested_target: int | None = None

    @staticmethod
    def _trace(name: str, **payload: object) -> None:
        try:
            from core.observability.runtime import global_telemetry

            global_telemetry.event(name, payload=payload)
        except Exception:
            # Observability must not make the Docker control plane fail open or
            # fail closed when its optional writer is unavailable.
            return

    def recommended_ready_target(self, *, arrival_rate_per_min: float, refill_p95_s: float) -> int:
        if self._adaptive_policy is None:
            return self._ready_target
        return self._adaptive_policy.target_for(
            arrival_rate_per_min=arrival_rate_per_min,
            refill_p95_s=refill_p95_s,
        )

    async def _effective_ready_target(self) -> int:
        if self._requested_target is not None:
            return self._requested_target
        arrival_rate = settings.NLP_AGENT_SANDBOX_ARRIVAL_RATE_PER_MIN
        refill_p95 = settings.NLP_AGENT_SANDBOX_REFILL_P95_S
        latest = getattr(self._metrics_store, "latest", None)
        if latest is not None:
            try:
                sample = await latest()
                if sample:
                    stamp = float(sample.get("timestamp", 0) or 0)
                    if stamp <= 0 or datetime.now(UTC).timestamp() - stamp <= 300:
                        arrival_rate = max(0.0, float(sample.get("arrival_rate_per_min", arrival_rate)))
                        refill_p95 = max(0.0, float(sample.get("refill_p95_s", refill_p95)))
                    else:
                        self._trace("sandbox.manager.metrics.stale", age_seconds=round(datetime.now(UTC).timestamp() - stamp, 1))
            except Exception as error:
                self._trace("sandbox.manager.metrics.unavailable", error=type(error).__name__)
        desired = self.recommended_ready_target(
            arrival_rate_per_min=arrival_rate,
            refill_p95_s=refill_p95,
        )
        if self._adaptive_policy is None or self._adaptive_state_store is None:
            return desired
        load = getattr(self._adaptive_state_store, "load", None)
        save = getattr(self._adaptive_state_store, "save", None)
        if load is None or save is None:
            return desired
        try:
            current, scaled_at = await load()
        except Exception as error:
            # Redis is an optimization input, never a reason to stop the
            # control plane.  Fall back to the deterministic target until the
            # next reconciliation can read state again.
            self._trace("sandbox.manager.adaptive_state.unavailable", error=type(error).__name__)
            return desired
        current = self._ready_target if current is None else current
        last = datetime.fromtimestamp(scaled_at, UTC) if scaled_at is not None else None
        now = datetime.now(UTC)
        if self._adaptive_policy.should_scale(
            current_target=current,
            desired_target=desired,
            last_scaled_at=last,
            now=now,
        ):
            try:
                await save(target=desired, scaled_at=now.timestamp())
            except Exception as error:
                self._trace("sandbox.manager.adaptive_state.save_failed", error=type(error).__name__)
                return desired
            return desired
        return current

    async def request_target(self, target: int) -> None:
        if target < 0:
            raise ValueError("pool target must be non-negative")
        upper = (
            self._adaptive_policy.ready_max
            if self._adaptive_policy is not None
            else max(self._ready_target, settings.NLP_AGENT_SANDBOX_WARM_POOL_READY_MAX)
        )
        self._requested_target = min(target, upper)

    async def refill(self) -> int:
        """Create only the deficit. Docker runs outside every database transaction."""
        self._trace("sandbox.manager.refill.started", profile=self._resource_profile_id)
        lock_name = f"nova.sandbox.pool.{self._resource_profile_id}"
        reserved_runtime_ids: list[str] = []
        async with self._session_factory() as lock_session:
            acquired = await lock_session.scalar(text("SELECT GET_LOCK(:name, 0)"), {"name": lock_name})
            if not acquired:
                return 0
            try:
                # Reserve rows on the advisory-lock connection before Docker
                # I/O.  Releasing the lock before provisioning avoids asking
                # a one-connection MySQL pool for a second connection.
                await self._fail_stale_creating(lock_session)
                counts = await self._counts(lock_session)
                target = await self._effective_ready_target()
                deficit = refill_deficit(
                    target=target,
                    ready_count=counts.ready_count,
                    creating_count=counts.creating_count,
                )
                for _ in range(deficit):
                    runtime_id = str(uuid4())
                    lock_session.add(
                        SandboxRuntimeInstanceModel(
                            id=runtime_id,
                            runtime_kind=self._runtime_kind,
                            image_digest=self._docker.image_digest,
                            resource_profile_id=self._resource_profile_id,
                            state="creating",
                        )
                    )
                    reserved_runtime_ids.append(runtime_id)
                await lock_session.commit()
            finally:
                await lock_session.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})
        for runtime_id in reserved_runtime_ids:
            await self._create_one(runtime_id=runtime_id)
        self._trace(
            "sandbox.manager.refill.completed",
            profile=self._resource_profile_id,
            created=len(reserved_runtime_ids),
        )
        return len(reserved_runtime_ids)

    async def capacity_snapshot(self) -> dict[str, int | str]:
        """Management-plane capacity data for dashboards and alert thresholds."""
        async with self._session_factory() as session:
            counts = await self._counts(session)
        target = await self._effective_ready_target()
        return {
            "resource_profile": self._resource_profile_id,
            "ready": counts.ready_count,
            "creating": counts.creating_count,
            "target": target,
            "deficit": refill_deficit(
                target=target,
                ready_count=counts.ready_count,
                creating_count=counts.creating_count,
            ),
            # Report the target actually selected after reading the durable
            # metrics/cooldown state, not the static environment estimate.
            "adaptive_target": target,
        }

    async def claim(self, scope: SandboxScope, *, lease_id: str) -> RuntimeClaim | None:
        self._trace("sandbox.manager.claim.started", lease_id=lease_id)
        claim = await self._claim_once(scope, lease_id=lease_id)
        if claim is None:
            await self.refill()
            claim = await self._claim_once(scope, lease_id=lease_id)
        if claim is None:
            self._trace("sandbox.manager.claim.warming", lease_id=lease_id)
            return None
        external_id = claim.runtime.external_runtime_id
        if external_id and await self._docker.kernel_ready(external_id):
            self._trace("sandbox.manager.claim.completed", lease_id=lease_id, runtime_id=str(claim.runtime.id))
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
                or lease.workspace_id != scope.workspace_id
                or lease.state != "active"
                or lease.expires_at <= _utc_now()
            ):
                return None
            auth_session = await session.get(SessionModel, lease.auth_session_id)
            user = await session.get(UserModel, lease.user_id)
            if not auth_lifecycle_allows_execution(
                lease=lease,
                auth_session=auth_session,
                user=user,
                scope_generation=scope.generation,
                scope_workspace_id=scope.workspace_id,
                now=_utc_now(),
            ):
                return None
            environment = await session.get(SandboxEnvironmentModel, lease.environment_id)
            if environment is None or lease.generation != environment.generation:
                return None
            claim = await warm_pool_service.claim(session, scope)
            if claim is not None:
                lease.runtime_instance_id = claim.runtime.id
            return claim

    async def destroy_runtime(self, runtime_id: str, *, reason: str, refill: bool = True) -> None:
        self._trace("sandbox.manager.destroy.started", runtime_id=runtime_id, reason=reason)
        async with self._session_factory.begin() as session:
            runtime = await warm_pool_service.mark_draining(session, runtime_id)
            if runtime is None:
                return
            external_id = runtime.external_runtime_id
        if external_id:
            try:
                self._faults.fail_if_configured("docker.destroy")
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
        # Reset performs a claim immediately after destroying the old
        # runtime.  It must not hold that request on a pool-wide advisory lock
        # while trying to refill; the next regular reconcile will replenish
        # capacity when needed.
        if refill:
            await self.refill()
        self._trace("sandbox.manager.destroy.completed", runtime_id=runtime_id, reason=reason)

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
        """Fence every existing ticket before destroying the user's runtime."""
        self._trace("sandbox.manager.reset.started", runtime_id=runtime_id, trace_id=trace_id, span_id=span_id)
        async with self._session_factory.begin() as session:
            runtime = await session.get(SandboxRuntimeInstanceModel, runtime_id, with_for_update=True)
            lease = await session.get(SandboxLeaseModel, lease_id, with_for_update=True)
            auth_session = None if lease is None else await session.get(SessionModel, lease.auth_session_id)
            user = None if lease is None else await session.get(UserModel, lease.user_id)
            if runtime is None:
                raise LookupError("sandbox runtime does not exist")
            if lease is None or not runtime_control_allows(
                scope=scope,
                lease=lease,
                runtime=runtime,
                expected_generation=generation,
                now=_utc_now(),
            ) or not auth_lifecycle_allows_execution(
                lease=lease,
                auth_session=auth_session,
                user=user,
                scope_generation=scope.generation,
                scope_workspace_id=scope.workspace_id,
                now=_utc_now(),
            ):
                raise PermissionError("sandbox reset is not authorized for this lease")
            if runtime.environment_id is None:
                raise LookupError("sandbox runtime has no environment")
            environment = await session.get(SandboxEnvironmentModel, runtime.environment_id, with_for_update=True)
            if environment is not None:
                environment.generation += 1
                leases = list(
                    (
                        await session.scalars(
                            select(SandboxLeaseModel)
                            .where(
                                SandboxLeaseModel.environment_id == environment.id,
                                SandboxLeaseModel.state == "active",
                            )
                            .with_for_update()
                        )
                    ).all()
                )
                for lease in leases:
                    # Rebind active leases to the new environment fence while
                    # clearing the destroyed runtime assignment. Old tickets
                    # still carry the previous runtime generation and fail.
                    lease.generation = environment.generation
                    lease.runtime_instance_id = None
                # Clear the authoritative pointer before the Docker destroy
                # transaction.  A concurrent claim can therefore not reuse
                # the old assigned runtime during the reset hand-off.
                environment.active_runtime_id = None
        await self.destroy_runtime(runtime_id, reason="user.restart", refill=False)
        self._trace("sandbox.manager.reset.completed", runtime_id=runtime_id, trace_id=trace_id, span_id=span_id)

    async def run_scratch(
        self,
        *,
        source: str,
        timeout_seconds: int = 15,
        output_limit_bytes: int = 1_000_000,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> dict[str, object]:
        """Run an isolated model experiment without claiming a user runtime."""
        self._faults.fail_if_configured("docker.scratch")
        self._trace("sandbox.manager.scratch.started", trace_id=trace_id, span_id=span_id)
        try:
            result = await self._docker.run_scratch(
                source=source,
                timeout_seconds=timeout_seconds,
                output_limit_bytes=output_limit_bytes,
            )
        except Exception as error:
            self._trace(
                "sandbox.manager.scratch.failed",
                trace_id=trace_id,
                span_id=span_id,
                error=type(error).__name__,
            )
            raise
        self._trace(
            "sandbox.manager.scratch.completed",
            trace_id=trace_id,
            span_id=span_id,
            status=result.get("status"),
        )
        return result

    async def interrupt_runtime(
        self,
        scope: SandboxScope,
        *,
        lease_id: str,
        runtime_id: str,
        generation: int | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        """Interrupt only a runtime owned by this manager's database row."""
        self._trace("sandbox.manager.interrupt.started", runtime_id=runtime_id, trace_id=trace_id, span_id=span_id)
        async with self._session_factory() as session:
            runtime = await session.get(SandboxRuntimeInstanceModel, runtime_id)
            lease = await session.get(SandboxLeaseModel, lease_id)
            auth_session = None if lease is None else await session.get(SessionModel, lease.auth_session_id)
            user = None if lease is None else await session.get(UserModel, lease.user_id)
            if runtime is None:
                raise LookupError("sandbox runtime does not exist")
            if lease is None or not runtime_control_allows(
                scope=scope,
                lease=lease,
                runtime=runtime,
                expected_generation=generation,
                now=_utc_now(),
            ) or not auth_lifecycle_allows_execution(
                lease=lease,
                auth_session=auth_session,
                user=user,
                scope_generation=scope.generation,
                scope_workspace_id=scope.workspace_id,
                now=_utc_now(),
            ):
                raise PermissionError("sandbox interrupt is not authorized for this lease")
            external_id = runtime.external_runtime_id
        if not external_id:
            raise LookupError("sandbox runtime has no Docker container")
        self._faults.fail_if_configured("docker.interrupt")
        await self._docker.interrupt(external_id)
        self._trace("sandbox.manager.interrupt.completed", runtime_id=runtime_id, trace_id=trace_id, span_id=span_id)

    async def reconcile(self) -> ReconcileActions:
        """Fail DB rows for vanished containers and remove unmanaged-in-DB orphans."""
        self._faults.fail_if_configured("manager.reconcile")
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
                self._faults.fail_if_configured("docker.destroy")
                await self._docker.destroy(external_id)
            except Exception:
                # It remains labelled and will be discovered again on the next pass.
                pass
        await self._retry_draining_runtimes()
        await self._destroy_unleased_assigned_runtimes()
        if self._metrics_store is not None:
            try:
                from .metrics import record_sandbox_capacity_sample

                await record_sandbox_capacity_sample(self._session_factory, store=self._metrics_store)
            except Exception as error:
                self._trace("sandbox.manager.metrics.record_failed", error=type(error).__name__)
        await self.refill()
        return actions

    async def _destroy_unleased_assigned_runtimes(self) -> None:
        """Lease expiry is authoritative even when an Outbox message was missed."""
        now = _utc_now()
        active_lease = exists(
            select(SandboxLeaseModel.id)
            .join(SessionModel, SessionModel.id == SandboxLeaseModel.auth_session_id)
            .join(UserModel, UserModel.id == SandboxLeaseModel.user_id)
            .where(
                SandboxLeaseModel.environment_id == SandboxRuntimeInstanceModel.environment_id,
                SandboxLeaseModel.state == "active",
                SandboxLeaseModel.expires_at > now,
                SessionModel.revoked_at.is_(None),
                SessionModel.expires_at > now,
                SessionModel.authorization_version == UserModel.authorization_version,
                UserModel.status == "active",
                UserModel.deleted_at.is_(None),
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
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> dict[str, object]:
        """Fence a one-time browser command before it ever reaches Docker."""
        self._trace(
            "sandbox.manager.execute.started",
            runtime_id=runtime_id,
            lease_id=lease_id,
            trace_id=trace_id,
            span_id=span_id,
        )
        async with self._session_factory.begin() as session:
            lease = await session.get(SandboxLeaseModel, lease_id, with_for_update=True)
            runtime = await session.get(SandboxRuntimeInstanceModel, runtime_id, with_for_update=True)
            if (
                lease is None or runtime is None or lease.user_id != scope.owner_user_id
                or lease.auth_session_id != scope.auth_session_id
                or lease.workspace_id != scope.workspace_id
                or lease.state != "active"
                or lease.generation != runtime.generation or runtime.generation != generation
                or runtime.state != RuntimeState.ASSIGNED
                or runtime.environment_id != lease.environment_id
            ):
                raise PermissionError("sandbox command lease, generation, or nonce is invalid")
            auth_session = None if lease is None else await session.get(SessionModel, lease.auth_session_id)
            user = None if lease is None else await session.get(UserModel, lease.user_id)
            if lease is None or not auth_lifecycle_allows_execution(
                lease=lease,
                auth_session=auth_session,
                user=user,
                scope_generation=scope.generation,
                scope_workspace_id=scope.workspace_id,
                now=_utc_now(),
            ):
                raise PermissionError("sandbox authentication lifecycle is no longer active")
            if runtime.claim_nonce_hash is not None:
                if nonce is None or not warm_pool_service.validate_nonce(runtime.claim_nonce_hash, nonce):
                    raise PermissionError("sandbox command lease, generation, or nonce is invalid")
                runtime.claim_nonce_hash = None
            external_id = runtime.external_runtime_id
        if not external_id:
            raise RuntimeError("claimed runtime has no container id")
        try:
            result = await self._docker.execute(external_id, source=source)
            self._trace(
                "sandbox.manager.execute.completed",
                runtime_id=runtime_id,
                status=result.get("status"),
                trace_id=trace_id,
                span_id=span_id,
            )
            return result
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
                self._faults.fail_if_configured("docker.destroy")
                await self._docker.destroy(external_id)
            except Exception as error:
                async with self._session_factory.begin() as session:
                    runtime = await session.get(SandboxRuntimeInstanceModel, runtime_id, with_for_update=True)
                    if runtime is not None:
                        runtime.failure_reason = f"destroy.retry: {error}"[:500]
                continue
            async with self._session_factory.begin() as session:
                await warm_pool_service.mark_destroyed(session, runtime_id)

    async def _fail_stale_creating(self, session: AsyncSession) -> None:
        deadline = _utc_now() - timedelta(minutes=5)
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

    async def _create_one(self, *, runtime_id: str | None = None) -> None:
        if runtime_id is None:
            runtime_id = str(uuid4())
            async with self._session_factory.begin() as session:
                session.add(
                    SandboxRuntimeInstanceModel(
                        id=runtime_id,
                        runtime_kind=self._runtime_kind,
                        image_digest=self._docker.image_digest,
                        resource_profile_id=self._resource_profile_id,
                        state="creating",
                    )
                )
        try:
            self._faults.fail_if_configured("docker.create")
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
                self._faults.fail_if_configured("docker.destroy")
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
            if runtime is not None and runtime.state in {"creating", "created"}:
                runtime.external_runtime_id = external_id
                runtime.state = ready_state_after_kernel_check(runtime.state)
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
