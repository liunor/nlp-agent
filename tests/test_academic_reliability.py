"""P2 reliability tests for shared state, fallback, retry, and metrics."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import time

import httpx
import pytest

from core.tool_config import (
    AcademicArxivConfig,
    AcademicReliabilityConfig,
    AcademicToolsConfig,
)
from server.tools.academic.arxiv import ArxivProvider
from server.tools.academic.contracts import (
    AcademicAuthor,
    AcademicIdentifiers,
    AcademicPaper,
    AcademicSearchInput,
    AcademicSourceRecord,
)
from server.tools.academic.provider import AcademicProviderError
from server.tools.academic.runtime import (
    AcademicMetrics,
    RedisAcademicStore,
    create_academic_redis_store,
)
from server.tools.academic.service import AcademicSearchService
from server.tools.web.cache import TTLCache


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.hashes: dict[str, dict[str, int]] = {}
        self.next_allowed: dict[str, float] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value: str, **_kwargs):
        self.values[key] = value
        return True

    async def eval(self, _script: str, _keys: int, key: str, interval_ms: int):
        now_ms = time.monotonic() * 1000
        next_ms = self.next_allowed.get(key, 0.0)
        if now_ms >= next_ms:
            self.next_allowed[key] = now_ms + interval_ms
            return 0
        return int(next_ms - now_ms) + 1

    async def hincrby(self, key: str, field: str, amount: int):
        bucket = self.hashes.setdefault(key, {})
        bucket[field] = bucket.get(field, 0) + amount
        return bucket[field]

    async def hset(self, key: str, *, mapping: dict[str, int]):
        self.hashes.setdefault(key, {}).update(mapping)
        return len(mapping)

    async def expire(self, _key: str, _ttl: int):
        return True

    async def aclose(self):
        return None


class UnavailableRedis(FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self.get_calls = 0

    async def get(self, key: str):
        del key
        self.get_calls += 1
        raise ConnectionError("redis unavailable")


def make_paper(title: str = "Attention Is All You Need") -> AcademicPaper:
    now = datetime.now(timezone.utc)
    return AcademicPaper(
        record_id="arxiv:1706.03762",
        title=title,
        authors=[AcademicAuthor(name="Ashish Vaswani")],
        publication_year=2017,
        publication_status="preprint",
        identifiers=AcademicIdentifiers(arxiv_id="1706.03762"),
        landing_url="https://arxiv.org/abs/1706.03762",
        source_records=[
            AcademicSourceRecord(
                source="arxiv",
                source_id="1706.03762v7",
                rank=1,
                retrieved_at=now,
            )
        ],
    )


class StaticProvider:
    name = "arxiv"

    def __init__(self) -> None:
        self.calls = 0
        self.paper = make_paper()

    async def search(self, _request: AcademicSearchInput):
        self.calls += 1
        return [self.paper]


class FailingProvider:
    name = "arxiv"

    def __init__(self, failures: int = 999) -> None:
        self.calls = 0
        self.failures = failures

    async def search(self, _request: AcademicSearchInput):
        self.calls += 1
        if self.calls <= self.failures:
            raise AcademicProviderError("temporary provider failure")
        return [make_paper()]


class BrokenSharedStore:
    async def get_cache(self, _key: str):
        raise ConnectionError("redis unavailable")

    async def put_cache(self, _key: str, _value: str, _ttl_s: int):
        raise ConnectionError("redis unavailable")

    async def put_metadata(self, _key: str, _value: str, _ttl_s: int):
        raise ConnectionError("redis unavailable")

    async def record_search(self, **_kwargs):
        raise ConnectionError("redis unavailable")

    async def record_provider(self, *_args, **_kwargs):
        raise ConnectionError("redis unavailable")


@pytest.mark.asyncio
async def test_redis_store_shares_cache_metadata_and_zero_charge_metrics():
    redis = FakeRedis()
    first = RedisAcademicStore(redis, operation_timeout_s=0.5)
    second = RedisAcademicStore(redis, operation_timeout_s=0.5)

    await first.put_cache("query", "response", 60)
    await first.put_metadata("paper", "metadata", 60)
    assert await second.get_cache("query") == "response"
    assert await second.get_metadata("paper") == "metadata"

    await first.record_search(cache_hit=True, partial=False)
    bucket = next(iter(redis.hashes.values()))
    assert bucket["searches"] == 1
    assert bucket["zero_charge_searches"] == 1
    assert bucket["billable_charges"] == 0


@pytest.mark.asyncio
async def test_redis_rate_limit_coordinates_independent_store_instances():
    redis = FakeRedis()
    first = RedisAcademicStore(redis, operation_timeout_s=0.5)
    second = RedisAcademicStore(redis, operation_timeout_s=0.5)
    completed: list[float] = []

    async def acquire(store: RedisAcademicStore):
        await store.acquire_rate_limit("arxiv", 0.02)
        completed.append(time.monotonic())

    await asyncio.gather(acquire(first), acquire(second))
    assert completed[1] - completed[0] >= 0.015


@pytest.mark.asyncio
async def test_redis_outage_enters_cooldown_instead_of_retrying_every_operation():
    redis = UnavailableRedis()
    store = RedisAcademicStore(
        redis, operation_timeout_s=0.5, unavailable_cooldown_s=60
    )

    with pytest.raises(ConnectionError, match="redis unavailable"):
        await store.get_cache("first")
    with pytest.raises(ConnectionError, match="fallback cooldown"):
        await store.get_cache("second")

    assert redis.get_calls == 1


def test_academic_redis_uses_gateway_server_url_when_env_is_unset(monkeypatch):
    monkeypatch.delenv("NLP_AGENT_REDIS_URL", raising=False)
    monkeypatch.setitem(
        __import__("sys").modules,
        "configs.settings",
        SimpleNamespace(settings=SimpleNamespace(gateway_runtime={"redis_url": "redis://redis-server:6379/2"})),
    )

    store = create_academic_redis_store(
        AcademicReliabilityConfig(redis_url_env="NLP_AGENT_REDIS_URL")
    )

    assert store is not None
    assert store._client.connection_pool.connection_kwargs["host"] == "redis-server"


@pytest.mark.asyncio
async def test_arxiv_fails_closed_when_distributed_limiter_is_unavailable():
    class BrokenRateLimiter:
        async def acquire_rate_limit(self, provider: str, interval_s: float) -> None:
            raise ConnectionError("redis unavailable")

    provider = ArxivProvider(
        config=AcademicArxivConfig(min_interval_s=3.0),
        transport=httpx.MockTransport(
            lambda _request: pytest.fail("network must not be called without a slot")
        ),
        shared_store=BrokenRateLimiter(),
    )

    with pytest.raises(
        AcademicProviderError,
        match="distributed rate limiter is unavailable",
    ):
        await provider.search(AcademicSearchInput(query="transformer"))


@pytest.mark.asyncio
async def test_service_falls_back_when_redis_is_unavailable():
    provider = StaticProvider()
    service = AcademicSearchService(
        AcademicToolsConfig(),
        providers=[provider],
        cache=TTLCache(ttl_s=0),
        shared_store=BrokenSharedStore(),
        metrics=AcademicMetrics(),
    )

    response = await service.search(AcademicSearchInput(query="transformer"))

    assert response.papers[0].identifiers.arxiv_id == "1706.03762"
    assert str(response.papers[0].scholar_url).startswith(
        "https://scholar.google.com/scholar?q="
    )
    assert response.partial is False
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_shared_metadata_cache_enriches_same_paper_across_different_queries():
    redis = FakeRedis()
    store = RedisAcademicStore(redis, operation_timeout_s=0.5)
    provider = StaticProvider()
    provider.paper = make_paper().model_copy(update={"abstract": "verified abstract"})
    service = AcademicSearchService(
        AcademicToolsConfig(),
        providers=[provider],
        cache=TTLCache(ttl_s=0),
        shared_store=store,
        metrics=AcademicMetrics(),
    )

    first = await service.search(AcademicSearchInput(query="transformer paper"))
    assert first.papers[0].abstract == "verified abstract"

    provider.paper = make_paper().model_copy(update={"abstract": None})
    second = await service.search(AcademicSearchInput(query="attention architecture"))

    assert provider.calls == 2
    assert second.papers[0].abstract == "verified abstract"


@pytest.mark.asyncio
async def test_shared_search_cache_is_reused_by_a_new_service_instance():
    redis = FakeRedis()
    first_provider = StaticProvider()
    second_provider = StaticProvider()
    first = AcademicSearchService(
        AcademicToolsConfig(),
        providers=[first_provider],
        cache=TTLCache(ttl_s=0),
        shared_store=RedisAcademicStore(redis, operation_timeout_s=0.5),
        metrics=AcademicMetrics(),
    )
    second = AcademicSearchService(
        AcademicToolsConfig(),
        providers=[second_provider],
        cache=TTLCache(ttl_s=0),
        shared_store=RedisAcademicStore(redis, operation_timeout_s=0.5),
        metrics=AcademicMetrics(),
    )
    request = AcademicSearchInput(query="transformer")

    await first.search(request)
    cached = await second.search(request)

    assert cached.papers
    assert first_provider.calls == 1
    assert second_provider.calls == 0


@pytest.mark.asyncio
async def test_provider_retry_can_recover_without_opening_circuit():
    provider = FailingProvider(failures=1)
    config = AcademicToolsConfig(
        reliability=AcademicReliabilityConfig(
            retry_attempts=2,
            retry_base_delay_s=0,
            retry_max_delay_s=0,
            circuit_failure_threshold=3,
        )
    )
    metrics = AcademicMetrics()
    service = AcademicSearchService(
        config,
        providers=[provider],
        cache=TTLCache(ttl_s=0),
        metrics=metrics,
    )

    response = await service.search(AcademicSearchInput(query="transformer"))

    assert response.papers
    assert provider.calls == 2
    snapshot = metrics.snapshot()
    assert snapshot["providers"]["arxiv"]["requests"] == 2
    assert snapshot["providers"]["arxiv"]["failures"] == 1
    assert snapshot["providers"]["arxiv"]["successes"] == 1


@pytest.mark.asyncio
async def test_provider_circuit_opens_after_repeated_failures():
    provider = FailingProvider()
    config = AcademicToolsConfig(
        reliability=AcademicReliabilityConfig(
            retry_attempts=1,
            circuit_failure_threshold=2,
            circuit_cooldown_s=60,
        )
    )
    service = AcademicSearchService(
        config,
        providers=[provider],
        cache=TTLCache(ttl_s=0),
        metrics=AcademicMetrics(),
    )
    request = AcademicSearchInput(query="transformer")

    await service.search(request)
    await service.search(request)
    third = await service.search(request)

    assert provider.calls == 2
    assert third.source_status[0].status == "circuit_open"


def test_metrics_report_hit_failure_latency_and_zero_charge_usage():
    metrics = AcademicMetrics()
    metrics.record_search(cache_hit=True, partial=False)
    metrics.record_search(cache_hit=False, partial=True)
    metrics.record_provider("arxiv", status="ok", latency_ms=10)
    metrics.record_provider("arxiv", status="timeout", latency_ms=30)

    snapshot = metrics.snapshot()
    assert snapshot["cache_hit_rate"] == 0.5
    assert snapshot["zero_charge_searches"] == 2
    assert snapshot["billable_charges"] == 0
    assert snapshot["providers"]["arxiv"]["failure_rate"] == 0.5
    assert snapshot["providers"]["arxiv"]["average_latency_ms"] == 20
