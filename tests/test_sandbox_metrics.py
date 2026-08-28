from __future__ import annotations

import asyncio


def test_redis_metrics_store_keeps_bounded_history() -> None:
    from server.sandbox.metrics import RedisSandboxMetricsStore

    class FakeRedis:
        def __init__(self):
            self.rows: list[tuple[float, str]] = []

        async def zadd(self, _key, values):
            self.rows.extend((score, member) for member, score in values.items())

        async def zremrangebyscore(self, _key, _minimum, maximum):
            self.rows[:] = [(score, member) for score, member in self.rows if score > maximum]

        async def expire(self, _key, _seconds):
            return True

        async def zrange(self, _key, start, end):
            values = [member for _, member in sorted(self.rows)]
            return values[start: None if end == -1 else end + 1]

    async def exercise():
        store = RedisSandboxMetricsStore(FakeRedis())
        await store.record({"timestamp": 1.0, "ready": 1})
        history = await store.record({"timestamp": 2.0, "ready": 2})
        latest = await store.latest()
        return history, latest

    history, latest = asyncio.run(exercise())
    assert [item["ready"] for item in history] == [1, 2]
    assert latest == {"timestamp": 2.0, "ready": 2}


def test_redis_metrics_store_can_read_history_without_recording_a_sample() -> None:
    from server.sandbox.metrics import RedisSandboxMetricsStore

    class ReadOnlyFakeRedis:
        async def zrange(self, _key, start, end):
            assert (start, end) == (-60, -1)
            return ['{"timestamp":1.0,"ready":1}']

    history = asyncio.run(RedisSandboxMetricsStore(ReadOnlyFakeRedis()).recent())

    assert history == [{"timestamp": 1.0, "ready": 1}]
