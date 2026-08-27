from __future__ import annotations

import asyncio


def test_execution_events_are_ordered_and_replayable_from_last_event_id() -> None:
    from server.sandbox.events import SandboxEventStore

    async def exercise():
        events = SandboxEventStore()
        first = await events.append("exec-1", user_id="user-a", event_type="execution.started", payload={})
        await events.append("exec-1", user_id="user-a", event_type="execution.output", payload={"text": "42"})
        return first, await events.replay("exec-1", user_id="user-a", after_event_id=first["event_id"])

    first, replay = asyncio.run(exercise())
    assert first["seq"] == 1
    assert replay == [{"event_id": "2", "seq": 2, "type": "execution.output", "payload": {"text": "42"}}]


def test_redis_stream_event_store_uses_owner_bound_stream_cursor() -> None:
    from server.sandbox.events import RedisSandboxEventStore

    class FakeRedis:
        def __init__(self):
            self.rows = []

        async def xadd(self, key, fields, maxlen, approximate):
            event_id = f"1-{len(self.rows)}"
            self.rows.append((event_id, fields))
            return event_id

        async def xrange(self, key, min, max, count):
            after = min[1:] if min.startswith("(") else "0-0"
            return [(event_id, fields) for event_id, fields in self.rows if event_id > after][:count]

        async def expire(self, key, seconds):
            return True

    async def exercise():
        redis = FakeRedis()
        events = RedisSandboxEventStore(redis, retention_seconds=60, max_events=10)
        first = await events.append("exec-1", user_id="user-a", event_type="execution.started", payload={})
        await events.append("exec-1", user_id="user-a", event_type="execution.output", payload={"text": "42"})
        replay = await events.replay("exec-1", user_id="user-a", after_event_id=first["event_id"])
        return first, replay

    first, replay = asyncio.run(exercise())
    assert first["event_id"] == "1-0"
    assert replay[0]["event_id"] == "1-1"
    assert replay[0]["payload"] == {"text": "42"}


def test_redis_event_store_factory_uses_memory_without_redis_url(monkeypatch) -> None:
    from configs.settings import settings
    from server.sandbox.events import SandboxEventStore, create_sandbox_event_store

    monkeypatch.setattr(settings, "NLP_AGENT_REDIS_URL", "")
    assert isinstance(create_sandbox_event_store(), SandboxEventStore)
