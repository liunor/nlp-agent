"""Opt-in integration checks for P2 shared academic-search state."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import os
import time
import uuid

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("NLP_AGENT_REDIS_INTEGRATION") != "1",
    reason="academic Redis integration is opt-in",
)


@pytest.mark.asyncio
async def test_shared_cache_and_rate_limit_survive_store_recreation():
    from redis.asyncio import Redis

    from server.tools.academic.runtime import RedisAcademicStore

    redis_url = os.environ["NLP_AGENT_REDIS_URL"]
    prefix = f"nova:test:academic:{uuid.uuid4().hex}"
    first_client = Redis.from_url(redis_url, decode_responses=True)
    second_client = Redis.from_url(redis_url, decode_responses=True)
    first = RedisAcademicStore(first_client, key_prefix=prefix)
    second = RedisAcademicStore(second_client, key_prefix=prefix)
    try:
        await first.put_cache("query", "payload", 60)
        assert await second.get_cache("query") == "payload"

        completed: list[float] = []

        async def acquire(store: RedisAcademicStore):
            await store.acquire_rate_limit("arxiv", 0.05)
            completed.append(time.monotonic())

        await asyncio.gather(acquire(first), acquire(second))
        assert completed[1] - completed[0] >= 0.04
    finally:
        with suppress(Exception):
            keys = await first_client.keys(f"{prefix}:*")
            if keys:
                await first_client.delete(*keys)
        with suppress(Exception):
            await first.close()
        with suppress(Exception):
            await second.close()
