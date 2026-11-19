"""Unit and integration tests for DeepSeek Search API web_search tool."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import core.model_runtime.factory as factory_module
from core.model_runtime.reporters import (
    InMemoryModelUsageReporter,
    ModelUsageReporterSlot,
)
from core.model_runtime.usage import (
    CanonicalTokenUsage,
    InvocationOutcome,
    UsageAttributionContext,
    bind_usage_attribution,
)
from core.observability.context import TelemetryContext, bind_telemetry_context
from core.observability.runtime import global_telemetry
from core.tool_config import WebSearchConfig, WebToolsConfig
from core.tool_runtime import ToolRisk, ToolScope, global_tool_runtime
from server.agent.compression.micro_compact import get_compactable_tool_names
from server.quota.builtin_pricing import BUILTIN_PRICING_RULES
from server.quota.pricing import PricingCatalog
from server.tools.academic.runtime import ProviderCircuitBreaker
from server.tools.web.cache import TTLCache
from server.tools.web.contracts import (
    UNTRUSTED_CONTENT_BANNER,
    TransientHttpError,
    WebAccessError,
    WebSearchInput,
)
from server.tools.web.search import WebSearchService


def _attribution() -> UsageAttributionContext:
    return UsageAttributionContext(
        request_id="req-search-1",
        user_id="user-search-1",
        workspace_id="ws-search-1",
        conversation_id="conv-search-1",
        turn_id="turn-search-1",
        reservation_id="res-search-1",
        worker_id="worker-search-1",
        purpose="worker",
    )


def _configure_reporter(monkeypatch) -> InMemoryModelUsageReporter:
    reporter = InMemoryModelUsageReporter()
    slot = ModelUsageReporterSlot(reporter)
    monkeypatch.setattr(
        factory_module,
        "get_global_model_factory",
        lambda: SimpleNamespace(reporter_slot=slot),
    )
    return reporter


async def test_search_builds_deepseek_payload_headers_and_truncates_ori_question():
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["content_type"] = request.headers.get("content-type")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "rerank_score": 2.6,
                        "title": "DeepSeek Search API Docs",
                        "url": "https://api-docs.deepseek.com/guides/search",
                        "snippet": "Short snippet.",
                        "summary": "Detailed model-oriented summary.",
                        "content": "",
                        "site_name": "DeepSeek Docs",
                        "site_icon": None,
                        "date": "2026-10-18T00:00:00+00:00",
                        "authority": 4,
                    }
                ]
            },
        )

    service = WebSearchService(
        WebToolsConfig(),
        transport=httpx.MockTransport(handler),
        cache=TTLCache(0),
        api_key="sk-deepseek-test",
    )
    long_question = "请帮我查询最新的 DeepSeek Search API 文档说明与计费细则 " * 6
    req = WebSearchInput(
        query='DeepSeek "Search API" site:api-docs.deepseek.com',
        ori_question=long_question,
        max_results=5,
    )
    res = await service.search(req)

    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.deepseek.com/v1/tools/search"
    assert captured["auth"] == "Bearer sk-deepseek-test"
    assert "application/json" in captured["content_type"]
    assert captured["body"]["query"] == 'DeepSeek "Search API" site:api-docs.deepseek.com'
    assert captured["body"]["count"] == 8
    assert captured["body"]["need_content"] is False
    assert len(captured["body"]["ori_question"]) == 128
    assert res.untrusted is True
    assert res.banner == UNTRUSTED_CONTENT_BANNER
    assert len(res.results) == 1
    assert res.results[0].rank == 1
    assert res.results[0].rerank_score == 2.6
    assert res.results[0].summary == "Detailed model-oriented summary."
    assert res.results[0].authority == 4


async def test_search_filters_low_score_deduplicates_urls_and_truncates_summaries():
    raw_items = [
        # Duplicate URL with lower score (should lose to the 2.7 entry below)
        {
            "rerank_score": 1.1,
            "title": "Duplicate Old",
            "url": "https://example.com/doc-1",
            "snippet": "dup snippet",
            "summary": "dup summary",
        },
        # Unsafe URLs (must be dropped regardless of high score)
        {
            "rerank_score": 2.9,
            "title": "Loopback SSRF",
            "url": "http://127.0.0.1/internal",
            "snippet": "bad",
            "summary": "bad",
        },
        {
            "rerank_score": 2.8,
            "title": "Bad Scheme",
            "url": "javascript:alert(1)",
            "snippet": "bad",
            "summary": "bad",
        },
        # Valid top 5 entries + 1 low-score noise (< 0.8)
        {
            "rerank_score": 2.7,
            "title": "Rank 1",
            "url": "https://example.com/doc-1",
            "snippet": "s1",
            "summary": "long summary 1",
        },
        {
            "rerank_score": 2.2,
            "title": "Rank 2",
            "url": "https://example.com/doc-2",
            "snippet": "s2",
            "summary": "long summary 2",
        },
        {
            "rerank_score": 1.8,
            "title": "Rank 3",
            "url": "https://example.com/doc-3",
            "snippet": "s3",
            "summary": "long summary 3",
        },
        {
            "rerank_score": 1.3,
            "title": "Rank 4",
            "url": "https://example.com/doc-4",
            "snippet": "s4",
            "summary": "long summary 4 should be cleared",
        },
        {
            "rerank_score": 0.9,
            "title": "Rank 5",
            "url": "https://example.com/doc-5",
            "snippet": "s5",
            "summary": "long summary 5 should be cleared",
        },
        {
            "rerank_score": 0.4,
            "title": "Noise Below Threshold",
            "url": "https://example.com/noise",
            "snippet": "noise",
            "summary": "noise summary",
        },
    ]

    service = WebSearchService(
        WebToolsConfig(),
        transport=httpx.MockTransport(
            lambda _req: httpx.Response(200, json={"results": raw_items})
        ),
        cache=TTLCache(0),
        api_key="sk-test",
    )
    res = await service.search(WebSearchInput(query="test query", max_results=5))

    assert [item.url for item in res.results] == [
        "https://example.com/doc-1",
        "https://example.com/doc-2",
        "https://example.com/doc-3",
        "https://example.com/doc-4",
        "https://example.com/doc-5",
    ]
    assert [item.rank for item in res.results] == [1, 2, 3, 4, 5]
    # Top 3 retain summary
    assert [item.summary for item in res.results[:3]] == [
        "long summary 1",
        "long summary 2",
        "long summary 3",
    ]
    # Rank 4~5 drop summary and keep snippet only
    assert res.results[3].summary == ""
    assert res.results[3].snippet == "s4"
    assert res.results[4].summary == ""
    assert res.results[4].snippet == "s5"


async def test_search_keeps_top_two_with_warning_when_all_scores_below_threshold():
    raw_items = [
        {
            "rerank_score": 0.6,
            "title": "Low A",
            "url": "https://example.com/a",
            "snippet": "sa",
            "summary": "sum a",
        },
        {
            "rerank_score": 0.5,
            "title": "Low B",
            "url": "https://example.com/b",
            "snippet": "sb",
            "summary": "sum b",
        },
        {
            "rerank_score": 0.2,
            "title": "Low C",
            "url": "https://example.com/c",
            "snippet": "sc",
            "summary": "sum c",
        },
    ]
    service = WebSearchService(
        WebToolsConfig(),
        transport=httpx.MockTransport(
            lambda _req: httpx.Response(200, json={"results": raw_items})
        ),
        cache=TTLCache(0),
        api_key="sk-test",
    )
    res = await service.search(WebSearchInput(query="obscure query"))

    assert len(res.results) == 2
    assert [item.url for item in res.results] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert any("低于阈值" in w for w in res.warnings)


async def test_singleflight_coalesces_concurrent_identical_queries_and_caches():
    http_calls = 0
    billable_calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        await asyncio.sleep(0.03)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "rerank_score": 2.1,
                        "title": "Shared Result",
                        "url": "https://example.com/shared",
                        "snippet": "short",
                        "summary": "shared summary",
                    }
                ]
            },
        )

    async def mark_billable() -> None:
        nonlocal billable_calls
        billable_calls += 1

    service = WebSearchService(
        WebToolsConfig(search=WebSearchConfig(cache_ttl_s=1800)),
        transport=httpx.MockTransport(handler),
        cache=TTLCache(1800),
        api_key="sk-test",
    )

    responses = await asyncio.gather(
        *(
            service.search(
                WebSearchInput(query="DeepSeek   V4", ori_question="DeepSeek V4 是什么"),
                on_billable_success=mark_billable,
            )
            for _ in range(5)
        )
    )

    assert http_calls == 1
    assert billable_calls == 1
    assert sum(1 for r in responses if not r.cache_hit) == 1
    assert sum(1 for r in responses if r.cache_hit) == 4

    # Subsequent normalized query hits TTL cache directly
    later = await service.search(
        WebSearchInput(query="  DeepSeek V4 ", ori_question="DeepSeek   V4 是什么"),
        on_billable_success=mark_billable,
    )
    assert later.cache_hit is True
    assert http_calls == 1
    assert billable_calls == 1


async def test_shared_redis_cache_serves_cross_process_hit_and_backfills_memory():
    class FakeSharedCache:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get_cache(self, key: str) -> str | None:
            return self.store.get(key)

        async def put_cache(self, key: str, value: str, ttl_s: int) -> None:
            assert ttl_s == 1800
            self.store[key] = value

    shared = FakeSharedCache()
    network_calls = 0

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal network_calls
        network_calls += 1
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "rerank_score": 2.0,
                        "title": "Redis Cached",
                        "url": "https://example.com/redis",
                        "snippet": "s",
                        "summary": "sum",
                    }
                ]
            },
        )

    writer = WebSearchService(
        WebToolsConfig(),
        transport=httpx.MockTransport(handler),
        cache=TTLCache(1800),
        shared_cache=shared,
        api_key="sk-test",
    )
    reader = WebSearchService(
        WebToolsConfig(),
        transport=httpx.MockTransport(
            lambda _req: pytest.fail("second worker must hit shared Redis cache")
        ),
        cache=TTLCache(1800),
        shared_cache=shared,
        api_key="sk-test",
    )

    first = await writer.search(WebSearchInput(query="redis test"))
    second = await reader.search(WebSearchInput(query="redis test"))

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.results[0].url == "https://example.com/redis"
    assert network_calls == 1


async def test_circuit_breaker_trips_after_consecutive_failures_and_recovers():
    attempts = 0
    should_fail = True

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if should_fail:
            return httpx.Response(503, text="service unavailable")
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "rerank_score": 2.0,
                        "title": "Recovered",
                        "url": "https://example.com/ok",
                        "snippet": "ok",
                        "summary": "ok summary",
                    }
                ]
            },
        )

    breaker = ProviderCircuitBreaker(failure_threshold=3, cooldown_s=60.0)
    service = WebSearchService(
        WebToolsConfig(),
        transport=httpx.MockTransport(handler),
        cache=TTLCache(0),
        circuit_breaker=breaker,
        api_key="sk-test",
    )

    for _ in range(3):
        with pytest.raises(TransientHttpError):
            await service.search(WebSearchInput(query="outage"))

    assert attempts == 3
    assert breaker.available() is False

    # 4th call fails fast without hitting transport
    with pytest.raises(WebAccessError) as exc_info:
        await service.search(WebSearchInput(query="outage"))
    assert exc_info.value.code == "circuit_open"
    assert attempts == 3

    # Simulate cooldown expiry -> recovery resets breaker
    breaker.opened_until = 0.0
    should_fail = False
    recovered = await service.search(WebSearchInput(query="outage"))
    assert len(recovered.results) == 1
    assert breaker.consecutive_failures == 0


async def test_tool_wrapper_bills_only_non_empty_cache_miss_and_prices_at_5000_micro(
    monkeypatch,
):
    import server.tools.api.web_search_tool as wrapper

    reporter = _configure_reporter(monkeypatch)
    cache = TTLCache(1800)
    mode = "non_empty"

    def handler(_req: httpx.Request) -> httpx.Response:
        if mode == "empty":
            return httpx.Response(200, json={"results": []})
        if mode == "auth_error":
            return httpx.Response(
                401, json={"error": {"message": "Invalid API key"}}
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "rerank_score": 2.3,
                        "title": "Billable Hit",
                        "url": "https://example.com/billable",
                        "snippet": "s",
                        "summary": "billable summary",
                    }
                ]
            },
        )

    service = WebSearchService(
        WebToolsConfig(),
        transport=httpx.MockTransport(handler),
        cache=cache,
        api_key="sk-test",
    )
    monkeypatch.setattr(wrapper, "build_search_service", lambda: service)

    with bind_usage_attribution(_attribution()):
        # 1. Empty results -> hold reserved then released, 0 settled events
        mode = "empty"
        empty_out = json.loads(
            await wrapper.web_search.ainvoke({"query": "no-matches-query"})
        )
        assert empty_out["results"] == []
        assert len(reporter.feature_reservations) == 1
        assert len(reporter.released_feature_reservations) == 1
        assert reporter.events == []

        # 2. HTTP 401 error -> hold reserved then released, 0 settled events
        mode = "auth_error"
        err_out = json.loads(
            await wrapper.web_search.ainvoke({"query": "auth-fail-query"})
        )
        assert err_out["code"] == "provider_auth_failed"
        assert len(reporter.feature_reservations) == 2
        assert len(reporter.released_feature_reservations) == 2
        assert reporter.events == []

        # 3. Non-empty HTTP 200 -> settles search_calls=1 under feature/web-search
        mode = "non_empty"
        ok_out = json.loads(
            await wrapper.web_search.ainvoke({"query": "paid-query"})
        )
        assert ok_out["cache_hit"] is False
        assert len(ok_out["results"]) == 1
        assert len(reporter.feature_reservations) == 3
        assert len(reporter.released_feature_reservations) == 2
        assert len(reporter.events) == 1

        # 4. Cache hit on same query -> no new reservation, no new settled event
        cached_out = json.loads(
            await wrapper.web_search.ainvoke({"query": "paid-query"})
        )
        assert cached_out["cache_hit"] is True
        assert len(reporter.feature_reservations) == 3
        assert len(reporter.events) == 1

    invocation, usage, outcome = reporter.events[0]
    assert invocation.identity.pricing_key == "feature/web-search"
    assert invocation.feature_usage.search_calls == 1
    catalog = PricingCatalog(BUILTIN_PRICING_RULES)
    priced = catalog.price(
        invocation.model_copy(
            update={"started_at": datetime(2026, 10, 20, tzinfo=timezone.utc)}
        ),
        CanonicalTokenUsage(source="provider"),
        InvocationOutcome(
            status=outcome.status,
            completed_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
        ),
    )
    assert priced.credits_micro == 5_000


async def test_web_search_descriptor_compactable_and_telemetry_span_annotations(
    monkeypatch,
):
    import server.tools.api.web_search_tool as wrapper
    import server.tools.tool_manager  # noqa: F401

    descriptor = global_tool_runtime.catalog.get("web_search")
    assert descriptor is not None
    assert descriptor.scopes == frozenset({ToolScope.WORKER})
    assert descriptor.capabilities == frozenset({"web.search"})
    assert descriptor.risk is ToolRisk.MEDIUM
    assert descriptor.read_only is True
    assert descriptor.concurrency_safe is True
    assert descriptor.timeout_s == 25
    assert descriptor.max_concurrency == 4
    assert "web_search" in get_compactable_tool_names()

    service = WebSearchService(
        WebToolsConfig(),
        transport=httpx.MockTransport(
            lambda _req: httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "rerank_score": 2.5,
                            "title": "Result One",
                            "url": "https://example.com/one",
                            "snippet": "s1",
                            "summary": "sum1",
                        },
                        {
                            "rerank_score": 1.9,
                            "title": "Result Two",
                            "url": "https://example.com/two",
                            "snippet": "s2",
                            "summary": "sum2",
                        },
                    ]
                },
            )
        ),
        cache=TTLCache(0),
        api_key="sk-test",
    )
    monkeypatch.setattr(wrapper, "build_search_service", lambda: service)
    _configure_reporter(monkeypatch)

    from core.tool_registry import physical_tool_manager

    physical_tool_manager.refresh_config()
    toolset = physical_tool_manager.get_worker_toolset(
        allowed_names={"web_search"},
        capabilities={"web.search"},
        session_id="sess-telemetry",
        profile="web_researcher",
        inherit_policy=False,
    )
    recorded_spans: list[Any] = []
    monkeypatch.setattr(
        global_telemetry,
        "emit_span",
        lambda span: recorded_spans.append(span),
    )

    telemetry_ctx = TelemetryContext(
        trace_id="trace-ws-1",
        span_id="span-parent-1",
        request_id="req-ws-1",
        session_id="sess-telemetry",
        turn_id="turn-ws-1",
        user_id="user-ws-1",
        workspace_id="ws-1",
        worker_id="worker-ws-1",
    )
    with bind_telemetry_context(telemetry_ctx), bind_usage_attribution(_attribution()):
        exec_result = await toolset.execute(
            "web_search",
            {"query": "DeepSeek API"},
            config={
                "configurable": {
                    "thread_id": "sess-telemetry",
                    "user_id": "user-ws-1",
                    "workspace_id": "ws-1",
                    "channel": "worker",
                    "worker_id": "worker-ws-1",
                }
            },
        )

    assert exec_result.ok is True
    tool_spans = [
        s
        for s in recorded_spans
        if s.name == "tool.web_search" and s.completed_at is not None
    ]
    assert len(tool_spans) == 1
    attrs = tool_spans[0].attributes
    assert attrs["result_urls"] == [
        "https://example.com/one",
        "https://example.com/two",
    ]
    assert attrs["result_count"] == 2
    assert attrs["cache_hit"] is False
