"""Durable, bounded capacity samples for the developer sandbox dashboard."""

from __future__ import annotations

import json
import time
from typing import Any

from configs.settings import settings
from sqlalchemy import func, select

from server.infrastructure.mysql.models import SandboxExecutionModel, SandboxRuntimeInstanceModel

from .faults import SandboxFaultInjector
from .optimization import AdaptivePoolPolicy


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
        bounded_limit = min(max(1, limit), self._max_samples, 60)
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
        arrivals = int(
            await session.scalar(
                select(func.count()).select_from(SandboxExecutionModel).where(
                    SandboxExecutionModel.started_at >= cutoff
                )
            )
            or 0
        )
    arrival_rate = round(arrivals / 5.0, 3)
    policy = AdaptivePoolPolicy(
        ready_min=settings.NLP_AGENT_SANDBOX_WARM_POOL_READY_MIN,
        ready_max=settings.NLP_AGENT_SANDBOX_WARM_POOL_READY_MAX,
        burst_buffer=settings.NLP_AGENT_SANDBOX_BURST_BUFFER,
    )
    adaptive_target = policy.target_for(
        arrival_rate_per_min=arrival_rate,
        refill_p95_s=settings.NLP_AGENT_SANDBOX_REFILL_P95_S,
    )
    sample = {
        "timestamp": now,
        "ready": sum(state == "ready_unbound" for state in states),
        "creating": sum(state == "creating" for state in states),
        "target": settings.NLP_AGENT_SANDBOX_WARM_POOL_READY_TARGET,
        "adaptive_target": adaptive_target,
        "deficit": max(0, adaptive_target - sum(state == "ready_unbound" for state in states)),
        "arrival_rate_per_min": arrival_rate,
        "refill_p95_s": settings.NLP_AGENT_SANDBOX_REFILL_P95_S,
    }
    try:
        await metrics_store.record(sample)
    except Exception:
        return


default_sandbox_metrics_store = create_sandbox_metrics_store()
