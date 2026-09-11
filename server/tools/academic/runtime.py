"""Production reliability primitives for free academic search."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import os
import threading
import time
from typing import Any, Protocol

from core.tool_config import AcademicReliabilityConfig


class AcademicSharedStore(Protocol):
    """Small Redis seam kept injectable for deterministic offline tests."""

    async def get_cache(self, key: str) -> str | None: ...

    async def put_cache(self, key: str, value: str, ttl_s: int) -> None: ...

    async def get_metadata(self, key: str) -> str | None: ...

    async def put_metadata(self, key: str, value: str, ttl_s: int) -> None: ...

    async def acquire_rate_limit(self, provider: str, interval_s: float) -> None: ...

    async def record_search(self, *, cache_hit: bool, partial: bool) -> None: ...

    async def record_provider(
        self, provider: str, *, status: str, latency_ms: int
    ) -> None: ...

    async def daily_metrics(self, day: str | None = None) -> dict[str, int]: ...

    async def close(self) -> None: ...


_RATE_LIMIT_LUA = """
local current = redis.call('TIME')
local now_ms = (tonumber(current[1]) * 1000) + math.floor(tonumber(current[2]) / 1000)
local next_ms = tonumber(redis.call('GET', KEYS[1]) or '0')
local interval_ms = tonumber(ARGV[1])
if now_ms >= next_ms then
  local updated = now_ms + interval_ms
  redis.call('PSETEX', KEYS[1], math.max(interval_ms * 2, 1000), tostring(updated))
  return 0
end
return next_ms - now_ms
"""


class RedisAcademicStore:
    """Shared cache, limiter, usage counters, and provider counters."""

    def __init__(
        self,
        client: Any,
        *,
        key_prefix: str = "nova:academic",
        operation_timeout_s: float = 1.0,
        unavailable_cooldown_s: float = 30.0,
    ) -> None:
        self._client = client
        self._prefix = key_prefix.rstrip(":")
        self._timeout_s = operation_timeout_s
        self._unavailable_cooldown_s = unavailable_cooldown_s
        self._unavailable_until = 0.0

    async def _bounded(self, operation: Any) -> Any:
        if time.monotonic() < self._unavailable_until:
            cancel = getattr(operation, "cancel", None)
            if cancel is not None:
                cancel()
            close = getattr(operation, "close", None)
            if close is not None:
                close()
            raise ConnectionError("academic Redis store is in fallback cooldown")
        try:
            result = await asyncio.wait_for(operation, timeout=self._timeout_s)
        except Exception:
            self._unavailable_until = time.monotonic() + self._unavailable_cooldown_s
            raise
        self._unavailable_until = 0.0
        return result

    async def get_cache(self, key: str) -> str | None:
        value = await self._bounded(self._client.get(f"{self._prefix}:cache:{key}"))
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value) if value is not None else None

    async def put_cache(self, key: str, value: str, ttl_s: int) -> None:
        if ttl_s <= 0:
            return
        await self._bounded(
            self._client.set(f"{self._prefix}:cache:{key}", value, ex=ttl_s)
        )

    async def get_metadata(self, key: str) -> str | None:
        value = await self._bounded(
            self._client.get(f"{self._prefix}:metadata:{key}")
        )
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value) if value is not None else None

    async def put_metadata(self, key: str, value: str, ttl_s: int) -> None:
        if ttl_s <= 0:
            return
        await self._bounded(
            self._client.set(f"{self._prefix}:metadata:{key}", value, ex=ttl_s)
        )

    async def acquire_rate_limit(self, provider: str, interval_s: float) -> None:
        interval_ms = max(1, int(interval_s * 1000))
        key = f"{self._prefix}:rate:{provider}"
        while True:
            wait_ms = int(
                await self._bounded(
                    self._client.eval(_RATE_LIMIT_LUA, 1, key, interval_ms)
                )
                or 0
            )
            if wait_ms <= 0:
                return
            await asyncio.sleep(wait_ms / 1000)

    def _daily_metrics_key(self, day: str | None = None) -> str:
        day = day or datetime.now(timezone.utc).date().isoformat()
        return f"{self._prefix}:metrics:{day}"

    async def _increment_metrics(self, values: dict[str, int]) -> None:
        key = self._daily_metrics_key()
        operations = [
            self._client.hincrby(key, field, amount)
            for field, amount in values.items()
        ]
        operations.extend(
            [
                self._client.hset(key, mapping={"billable_charges": 0}),
                self._client.expire(key, 90 * 24 * 3600),
            ]
        )
        await self._bounded(asyncio.gather(*operations))

    async def record_search(self, *, cache_hit: bool, partial: bool) -> None:
        await self._increment_metrics(
            {
                "searches": 1,
                "cache_hits": int(cache_hit),
                "partial_results": int(partial),
                "zero_charge_searches": 1,
            }
        )

    async def record_provider(
        self, provider: str, *, status: str, latency_ms: int
    ) -> None:
        safe_provider = provider.replace(":", "_")
        safe_status = status.replace(":", "_")
        await self._increment_metrics(
            {
                f"provider:{safe_provider}:requests": 1,
                f"provider:{safe_provider}:status:{safe_status}": 1,
                f"provider:{safe_provider}:latency_ms": max(0, latency_ms),
            }
        )

    async def daily_metrics(self, day: str | None = None) -> dict[str, int]:
        raw = await self._bounded(self._client.hgetall(self._daily_metrics_key(day)))
        if not isinstance(raw, dict):
            return {}
        metrics: dict[str, int] = {}
        for key, value in raw.items():
            clean_key = key.decode("utf-8") if isinstance(key, bytes) else str(key)
            try:
                metrics[clean_key] = int(value)
            except (TypeError, ValueError):
                continue
        return metrics

    async def close(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()


@dataclass
class _ProviderMetrics:
    requests: int = 0
    successes: int = 0
    failures: int = 0
    timeouts: int = 0
    rate_limited: int = 0
    circuit_open: int = 0
    latency_ms: int = 0


class AcademicMetrics:
    """Process-local live metrics; Redis receives the durable daily counters."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._providers: dict[str, _ProviderMetrics] = defaultdict(_ProviderMetrics)
        self._searches = 0
        self._cache_hits = 0
        self._partial_results = 0

    def record_search(self, *, cache_hit: bool, partial: bool) -> None:
        with self._lock:
            self._searches += 1
            self._cache_hits += int(cache_hit)
            self._partial_results += int(partial)

    def record_provider(self, provider: str, *, status: str, latency_ms: int) -> None:
        with self._lock:
            item = self._providers[provider]
            item.requests += 1
            item.latency_ms += max(0, latency_ms)
            if status == "ok":
                item.successes += 1
            else:
                item.failures += 1
                item.timeouts += int(status == "timeout")
                item.rate_limited += int(status == "rate_limited")
                item.circuit_open += int(status == "circuit_open")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            providers = {
                name: {
                    "requests": item.requests,
                    "successes": item.successes,
                    "failures": item.failures,
                    "failure_rate": item.failures / item.requests if item.requests else 0.0,
                    "average_latency_ms": item.latency_ms / item.requests
                    if item.requests
                    else 0.0,
                    "timeouts": item.timeouts,
                    "rate_limited": item.rate_limited,
                    "circuit_open": item.circuit_open,
                }
                for name, item in self._providers.items()
            }
            return {
                "searches": self._searches,
                "zero_charge_searches": self._searches,
                "billable_charges": 0,
                "cache_hits": self._cache_hits,
                "cache_hit_rate": self._cache_hits / self._searches
                if self._searches
                else 0.0,
                "partial_results": self._partial_results,
                "providers": providers,
            }


class ProviderCircuitBreaker:
    def __init__(self, *, failure_threshold: int, cooldown_s: float) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self.consecutive_failures = 0
        self.opened_until = 0.0

    def available(self) -> bool:
        return time.monotonic() >= self.opened_until

    def succeed(self) -> None:
        self.consecutive_failures = 0
        self.opened_until = 0.0

    def fail(self, *, cooldown_s: float | None = None) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self.opened_until = time.monotonic() + max(
                self.cooldown_s, cooldown_s or 0.0
            )


global_academic_metrics = AcademicMetrics()


def create_academic_redis_store(
    config: AcademicReliabilityConfig,
) -> RedisAcademicStore | None:
    if not config.redis_enabled:
        return None
    redis_url = os.environ.get(config.redis_url_env, "").strip()
    if not redis_url:
        try:
            from configs.settings import settings

            redis_url = str(
                settings.gateway_runtime.get("redis_url", "")
            ).strip()
        except Exception:
            redis_url = ""
    if not redis_url:
        return None
    from redis.asyncio import Redis

    return RedisAcademicStore(
        Redis.from_url(redis_url, decode_responses=True),
        key_prefix=config.redis_key_prefix,
        operation_timeout_s=config.redis_operation_timeout_s,
        unavailable_cooldown_s=config.circuit_cooldown_s,
    )
