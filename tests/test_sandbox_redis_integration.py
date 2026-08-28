"""Opt-in Redis Stream integration checks for the multi-instance event seam."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_SANDBOX_REDIS_INTEGRATION") != "1",
    reason="Redis Stream integration is enabled in CI only",
)


@pytest.mark.asyncio
async def test_redis_stream_events_survive_store_recreation() -> None:
    from redis.asyncio import Redis

    from server.sandbox.events import RedisSandboxEventStore

    client = Redis.from_url(os.environ["NLP_AGENT_REDIS_URL"], decode_responses=True)
    execution_id = f"ci-{uuid4().hex}"
    store = RedisSandboxEventStore(client, retention_seconds=60, max_events=100)
    try:
        first = await store.append(execution_id, user_id="user-a", event_type="execution.started", payload={})
        await store.append(execution_id, user_id="user-a", event_type="execution.output", payload={"text": "42"})
        recreated = RedisSandboxEventStore(client, retention_seconds=60, max_events=100)
        events = await recreated.replay(execution_id, user_id="user-a", after_event_id=first["event_id"])
        assert events[0]["payload"] == {"text": "42"}
        assert await client.exists(f"nova:sandbox:events:{execution_id}")
    finally:
        await client.delete(f"nova:sandbox:events:{execution_id}")
        await client.aclose()
