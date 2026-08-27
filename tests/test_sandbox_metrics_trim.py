from __future__ import annotations

import json

import pytest


class _FakeRedis:
    def __init__(self) -> None:
        self.rows: list[tuple[float, str]] = []

    async def zadd(self, _key: str, values: dict[str, float]) -> None:
        self.rows.extend((score, member) for member, score in values.items())
        self.rows.sort()

    async def zremrangebyscore(self, _key: str, _low: float, _high: float) -> None:
        return None

    async def zcard(self, _key: str) -> int:
        return len(self.rows)

    async def zremrangebyrank(self, _key: str, start: int, end: int) -> None:
        del self.rows[start : end + 1]

    async def expire(self, _key: str, _seconds: int) -> None:
        return None

    async def zrange(self, _key: str, start: int, end: int) -> list[str]:
        size = len(self.rows)
        start = start if start >= 0 else size + start
        end = end if end >= 0 else size + end
        return [member for _, member in self.rows[start : end + 1]]


@pytest.mark.asyncio
async def test_metrics_store_trims_only_oldest_excess_samples() -> None:
    from server.sandbox.metrics import RedisSandboxMetricsStore

    redis = _FakeRedis()
    store = RedisSandboxMetricsStore(redis, max_samples=100)
    for timestamp in range(105):
        await store.record({"timestamp": timestamp, "ready": timestamp})
    assert len(redis.rows) == 100
    assert [json.loads(member)["ready"] for _, member in redis.rows[:3]] == [5, 6, 7]
