"""Durable, bounded capacity samples for the developer sandbox dashboard."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable

from configs.settings import settings
from sqlalchemy import func, or_, select

from server.infrastructure.mysql.models import (
    RoleModel,
    SessionModel,
    SandboxLeaseModel,
    SandboxRuntimeInstanceModel,
    UserModel,
    UserRoleModel,
)

from .faults import SandboxFaultInjector
from .optimization import AdaptivePoolPolicy, SANDBOX_ROLE_PRIORITY


@dataclass(frozen=True, slots=True)
class SandboxLeaseDemand:
    """Online user demand split from already-assigned Runtime capacity."""

    online_count: int
    unassigned_count: int
    role_demand: dict[str, int]
    active_session_count: int = 0


def aggregate_sandbox_capacity_samples(
    samples: Iterable[dict[str, object]],
    *,
    now: float | None = None,
    window_seconds: int = 30 * 60,
    bucket_seconds: int = 30,
    max_points: int = 60,
) -> list[dict[str, object]]:
    """Return a stable, bounded view of real capacity samples.

    Capacity gauges are sampled more frequently than a human can read.  The
    dashboard therefore keeps the newest real sample in each time bucket,
    instead of drawing every short-lived fluctuation or inventing empty
    points.  The returned timestamp always came from an input sample.
    """
    if window_seconds <= 0 or bucket_seconds <= 0 or max_points <= 0:
        raise ValueError("window, bucket, and point limits must be positive")
    end = time.time() if now is None else float(now)
    if not math.isfinite(end):
        raise ValueError("now must be finite")
    cutoff = end - window_seconds
    buckets: dict[int, tuple[float, dict[str, object]]] = {}
    for raw in samples:
        try:
            timestamp = float(raw.get("timestamp", 0) or 0)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(timestamp) or timestamp < cutoff or timestamp > end:
            continue
        bucket = int(timestamp // bucket_seconds)
        previous = buckets.get(bucket)
        if previous is None or timestamp >= previous[0]:
            sample = dict(raw)
            sample["timestamp"] = timestamp
            buckets[bucket] = (timestamp, sample)
    return [sample for _timestamp, sample in sorted(buckets.values())[-max_points:]]


def summarize_sandbox_lease_demand(
    lease_rows: Any,
) -> SandboxLeaseDemand:
    """Collapse each active Lease to its highest effective role."""
    online_count = 0
    unassigned_count = 0
    role_demand: dict[str, int] = {}
    for role_codes, assigned in lease_rows:
        online_count += 1
        if assigned:
            continue
        unassigned_count += 1
        if isinstance(role_codes, str):
            normalized_roles = (role_codes,)
        else:
            normalized_roles = tuple(role_codes)
        role = min(
            (str(code).strip().lower() for code in normalized_roles),
            key=lambda code: SANDBOX_ROLE_PRIORITY.get(code, SANDBOX_ROLE_PRIORITY["guest"]),
            default="guest",
        )
        if role not in SANDBOX_ROLE_PRIORITY:
            role = "guest"
        role_demand[role] = role_demand.get(role, 0) + 1
    return SandboxLeaseDemand(
        online_count=online_count,
        unassigned_count=unassigned_count,
        role_demand=role_demand,
        active_session_count=online_count,
    )


async def collect_sandbox_lease_demand(
    session: Any,
    *,
    now: datetime | None = None,
) -> SandboxLeaseDemand:
    """Read active online Leases and resolve one effective role per user."""
    current_time = (now or datetime.now(UTC)).replace(tzinfo=None)
    active_rows = (
        await session.execute(
            select(SandboxLeaseModel.user_id, SandboxLeaseModel.runtime_instance_id)
            .join(SessionModel, SessionModel.id == SandboxLeaseModel.auth_session_id)
            .join(UserModel, UserModel.id == SandboxLeaseModel.user_id)
            .where(
                SandboxLeaseModel.state == "active",
                SandboxLeaseModel.expires_at > current_time,
                SessionModel.revoked_at.is_(None),
                SessionModel.expires_at > current_time,
                SessionModel.authorization_version == UserModel.authorization_version,
                UserModel.status == "active",
                UserModel.deleted_at.is_(None),
            )
        )
    ).all()
    user_ids = {str(user_id) for user_id, _runtime_id in active_rows}
    roles_by_user: dict[str, set[str]] = {}
    if user_ids:
        role_rows = (
            await session.execute(
                select(UserRoleModel.user_id, RoleModel.code)
                .join(RoleModel, RoleModel.id == UserRoleModel.role_id)
                .where(
                    UserRoleModel.user_id.in_(user_ids),
                    RoleModel.status == "active",
                    or_(
                        UserRoleModel.expires_at.is_(None),
                        UserRoleModel.expires_at > current_time,
                    ),
                )
            )
        ).all()
        for user_id, role_code in role_rows:
            roles_by_user.setdefault(str(user_id), set()).add(str(role_code))
    sessions_by_user: dict[str, bool] = {}
    for user_id, runtime_id in active_rows:
        key = str(user_id)
        # A user with multiple sessions is online once; they are considered
        # assigned when any active session already owns a Runtime.
        sessions_by_user[key] = sessions_by_user.get(key, False) or runtime_id is not None
    demand = summarize_sandbox_lease_demand(
        (
            roles_by_user.get(user_id, {"guest"}),
            assigned,
        )
        for user_id, assigned in sessions_by_user.items()
    )
    return SandboxLeaseDemand(
        online_count=demand.online_count,
        unassigned_count=demand.unassigned_count,
        role_demand=demand.role_demand,
        active_session_count=len(active_rows),
    )


def sandbox_arrival_rate_per_min(*, new_lease_count: int, window_seconds: int) -> float:
    """Convert newly-created Sandbox leases into a bounded per-minute rate.

    Code executions on an already-assigned Kernel are deliberately excluded:
    they do not create demand for another warm Runtime.
    """
    if new_lease_count < 0 or window_seconds <= 0:
        raise ValueError("lease count must be non-negative and window must be positive")
    return round(new_lease_count * 60.0 / window_seconds, 3)


class RedisSandboxMetricsStore:
    def __init__(
        self,
        client: Any,
        *,
        key: str = "nova:sandbox:metrics:capacity",
        retention_seconds: int = 7 * 24 * 3600,
        max_samples: int = 2_000,
        fault_injector: SandboxFaultInjector | None = None,
    ) -> None:
        self._client = client
        self._key = key
        self._retention_seconds = max(60, retention_seconds)
        self._max_samples = max(100, max_samples)
        self._faults = fault_injector or SandboxFaultInjector.from_env()

    async def record(self, sample: dict[str, object]) -> list[dict[str, object]]:
        self._faults.fail_if_configured("redis.metrics")
        timestamp = float(sample.get("timestamp", time.time()))
        member = json.dumps(sample, separators=(",", ":"), sort_keys=True)
        await self._client.zadd(self._key, {member: timestamp})
        await self._client.zremrangebyscore(self._key, 0, timestamp - self._retention_seconds)
        trim = getattr(self._client, "zremrangebyrank", None)
        card = getattr(self._client, "zcard", None)
        if trim is not None and card is not None:
            count = int(await card(self._key))
            if count > self._max_samples:
                # Redis rank endpoints are inclusive.  Remove precisely the
                # oldest excess rows; using a negative end rank can delete the
                # entire sorted set when it is smaller than max_samples.
                await trim(self._key, 0, count - self._max_samples - 1)
        await self._client.expire(self._key, self._retention_seconds)
        rows = await self._client.zrange(self._key, -min(self._max_samples, 60), -1)
        return self._decode_rows(rows)

    @staticmethod
    def _decode_rows(rows: list[Any]) -> list[dict[str, object]]:
        samples: list[dict[str, object]] = []
        for row in rows:
            try:
                value = row.decode("utf-8") if isinstance(row, bytes) else row
                parsed = json.loads(value)
            except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict):
                samples.append(parsed)
        return samples

    async def recent(self, limit: int = 60) -> list[dict[str, object]]:
        """Read recent samples without extending or mutating the series."""
        self._faults.fail_if_configured("redis.metrics.read")
        bounded_limit = min(max(1, limit), self._max_samples)
        rows = await self._client.zrange(self._key, -bounded_limit, -1)
        return self._decode_rows(rows)

    async def latest(self) -> dict[str, object] | None:
        """Read the most recent bounded sample for Manager feedback."""
        samples = await self.recent(1)
        return samples[-1] if samples else None

    async def close(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()


class RedisSandboxAdaptiveStateStore:
    """Persist adaptive target/cooldown state outside the Web process."""

    def __init__(self, client: Any, *, key: str = "nova:sandbox:capacity:adaptive", fault_injector: SandboxFaultInjector | None = None) -> None:
        self._client = client
        self._key = key
        self._faults = fault_injector or SandboxFaultInjector.from_env()

    async def load(self) -> tuple[int | None, float | None]:
        self._faults.fail_if_configured("redis.state.read")
        values = await self._client.hgetall(self._key)
        if not values:
            return None, None
        def value(name: str) -> str | None:
            raw = values.get(name) if isinstance(values, dict) else None
            if isinstance(raw, bytes):
                return raw.decode("utf-8")
            return str(raw) if raw is not None else None
        target = value("target")
        scaled_at = value("scaled_at")
        try:
            parsed_target = int(target) if target is not None else None
        except ValueError:
            parsed_target = None
        try:
            parsed_scaled_at = float(scaled_at) if scaled_at is not None else None
        except ValueError:
            parsed_scaled_at = None
        return parsed_target, parsed_scaled_at

    async def save(self, *, target: int, scaled_at: float) -> None:
        self._faults.fail_if_configured("redis.state.write")
        await self._client.hset(self._key, mapping={"target": target, "scaled_at": scaled_at})

    async def close(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()


def create_sandbox_adaptive_state_store() -> RedisSandboxAdaptiveStateStore | None:
    redis_url = settings.NLP_AGENT_REDIS_URL.strip()
    if not redis_url:
        return None
    from redis.asyncio import Redis

    return RedisSandboxAdaptiveStateStore(Redis.from_url(redis_url, decode_responses=True))


def create_sandbox_metrics_store() -> RedisSandboxMetricsStore | None:
    redis_url = settings.NLP_AGENT_REDIS_URL.strip()
    if not redis_url:
        return None
    from redis.asyncio import Redis

    return RedisSandboxMetricsStore(
        Redis.from_url(redis_url, decode_responses=True),
        retention_seconds=settings.NLP_AGENT_SANDBOX_METRICS_RETENTION_S,
    )


async def record_sandbox_capacity_sample(session_factory: Any, *, store: Any | None = None) -> None:
    """Collect adaptive inputs on a timer, independent of dashboard visits."""
    metrics_store = store if store is not None else default_sandbox_metrics_store
    if metrics_store is None:
        return
    now = time.time()
    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
    async with session_factory() as session:
        states = list((await session.scalars(select(SandboxRuntimeInstanceModel.state))).all())
        demand = await collect_sandbox_lease_demand(session)
        new_leases = int(
            await session.scalar(
                select(func.count()).select_from(SandboxLeaseModel).where(
                    SandboxLeaseModel.created_at >= cutoff
                )
            )
            or 0
        )
    arrival_rate = sandbox_arrival_rate_per_min(new_lease_count=new_leases, window_seconds=300)
    policy = AdaptivePoolPolicy(
        ready_min=settings.NLP_AGENT_SANDBOX_WARM_POOL_READY_MIN,
        ready_max=settings.NLP_AGENT_SANDBOX_WARM_POOL_READY_MAX,
        burst_buffer=settings.NLP_AGENT_SANDBOX_BURST_BUFFER,
    )
    adaptive_target = policy.target_for(
        arrival_rate_per_min=arrival_rate,
        refill_p95_s=settings.NLP_AGENT_SANDBOX_REFILL_P95_S,
        unassigned_lease_count=demand.unassigned_count,
    )
    sample = {
        "timestamp": now,
        "ready": sum(state == "ready_unbound" for state in states),
        "creating": sum(state == "creating" for state in states),
        "assigned": sum(state == "assigned" for state in states),
        "total": sum(state in {"ready_unbound", "creating", "claiming", "assigned", "draining"} for state in states),
        "total_max": settings.NLP_AGENT_SANDBOX_RUNTIME_TOTAL_MAX,
        "online_count": demand.online_count,
        "active_session_count": demand.active_session_count,
        "unassigned_count": demand.unassigned_count,
        "role_demand": demand.role_demand,
        "target": settings.NLP_AGENT_SANDBOX_WARM_POOL_READY_TARGET,
        "adaptive_target": adaptive_target,
        "deficit": max(0, adaptive_target - sum(state == "ready_unbound" for state in states)),
        "arrival_rate_per_min": arrival_rate,
        "new_lease_count": new_leases,
        "refill_p95_s": settings.NLP_AGENT_SANDBOX_REFILL_P95_S,
    }
    try:
        await metrics_store.record(sample)
    except Exception:
        return


default_sandbox_metrics_store = create_sandbox_metrics_store()
