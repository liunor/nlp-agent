"""Search service tests: provider selection, fallback, normalization, adapters."""

from __future__ import annotations

import json

import httpx
import pytest

from core.tool_config import (
    WebSearchConfig,
    WebSearchProviderConfig,
    WebToolsConfig,
)
from server.tools.web import providers as providers_module
from server.tools.web import search as search_module
from server.tools.web.cache import TTLCache
from server.tools.web.contracts import (
    SearchRequest,
    SearchResult,
    WebAccessError,
    WebSearchInput,
)
from server.tools.web.providers import (
    ProviderNotConfigured,
    SearxngProvider,
    TavilyProvider,
    clean_snippet,
    normalize_title,
)
from server.tools.web.search import WebSearchService


class FakeProvider:
    def __init__(self, provider_id: str, results=None, error: Exception | None = None):
        self.id = provider_id
        self.results = results or []
        self.error = error
        self.calls = 0

    async def search(self, request: SearchRequest):
        self.calls += 1
        if self.error is not None:
            raise self.error
        from server.tools.web.contracts import ProviderSearchResult

        return ProviderSearchResult(
            provider=self.id, results=list(self.results), total_results=len(self.results)
        )


def _config(
    *,
    allow_override: bool = False,
    default: str = "fake",
    fallback: list[str] | None = None,
    cache_ttl: int = 0,
) -> WebToolsConfig:
    provider_specs = {
        name: WebSearchProviderConfig(enabled=True)
        for name in [default, *(fallback or [])]
    }
    return WebToolsConfig(
        allow_provider_override=allow_override,
        search=WebSearchConfig(
            default_provider=default,
            fallback_providers=fallback or [],
            cache_ttl_s=cache_ttl,
            providers=provider_specs,
        ),
    )


def _result(url: str, title: str = "t", snippet: str = "s") -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet, source="fake")


async def test_search_returns_normalized_results_with_provider():
    provider = FakeProvider(
        "fake",
        results=[
            _result("https://a.com/1"),
            _result("https://a.com/1#frag"),
            _result("https://a.com/1/"),
            _result("https://b.com/2"),
        ],
    )
    service = WebSearchService(_config(), providers={"fake": provider})
    response = await service.search(WebSearchInput(query="q", max_results=10))
    assert response.provider == "fake"
    urls = [item.url for item in response.results]
    assert urls == ["https://a.com/1", "https://b.com/2"]
    assert response.total_results == 2


async def test_search_caps_max_results():
    provider = FakeProvider("fake", results=[_result(f"https://a.com/{i}") for i in range(10)])
    service = WebSearchService(_config(), providers={"fake": provider})
    response = await service.search(WebSearchInput(query="q", max_results=3))
    assert response.total_results == 3


async def test_search_falls_back_on_transient_failure():
    broken = FakeProvider("fake", error=httpx.ConnectError("boom"))
    healthy = FakeProvider("backup", results=[_result("https://b.com/")])
    service = WebSearchService(
        _config(fallback=["backup"]),
        providers={"fake": broken, "backup": healthy},
    )
    response = await service.search(WebSearchInput(query="q"))
    assert response.provider == "backup"
    assert any("fake" in warning for warning in response.warnings)


async def test_search_raises_when_all_providers_fail_transiently():
    broken = FakeProvider("fake", error=httpx.ConnectError("boom"))
    service = WebSearchService(_config(), providers={"fake": broken})
    with pytest.raises(ConnectionError):
        await service.search(WebSearchInput(query="q"))


async def test_search_skips_unconfigured_provider_and_errors_when_none_left():
    unconfigured = FakeProvider(
        "fake", error=ProviderNotConfigured("fake", "missing key")
    )
    service = WebSearchService(_config(), providers={"fake": unconfigured})
    with pytest.raises(WebAccessError) as excinfo:
        await service.search(WebSearchInput(query="q"))
    assert excinfo.value.code == "provider_unconfigured"


async def test_provider_override_is_ignored_when_not_allowed():
    default = FakeProvider("fake", results=[_result("https://a.com/")])
    other = FakeProvider("backup", results=[_result("https://b.com/")])
    service = WebSearchService(
        _config(fallback=["backup"], allow_override=False),
        providers={"fake": default, "backup": other},
    )
    response = await service.search(
        WebSearchInput(query="q", provider="backup")
    )
    assert response.provider == "fake"
    assert any("忽略" in warning for warning in response.warnings)


async def test_provider_override_is_honored_when_allowed():
    default = FakeProvider("fake", results=[_result("https://a.com/")])
    other = FakeProvider("backup", results=[_result("https://b.com/")])
    service = WebSearchService(
        _config(fallback=["backup"], allow_override=True),
        providers={"fake": default, "backup": other},
    )
    response = await service.search(WebSearchInput(query="q", provider="backup"))
    assert response.provider == "backup"


async def test_search_caches_by_normalized_request():
    provider = FakeProvider("fake", results=[_result("https://a.com/")])
    service = WebSearchService(
        _config(cache_ttl=60), providers={"fake": provider}
    )
    first = await service.search(WebSearchInput(query="  Hello World "))
    second = await service.search(WebSearchInput(query="hello world"))
    assert provider.calls == 1
    assert second.results == first.results


def test_clean_snippet_strips_html_and_truncates():
    raw = "<b>Bold</b>   text&amp;more " + "x" * 3000
    snippet = clean_snippet(raw)
    assert "<b>" not in snippet
    assert "Bold text&more" in snippet
    assert len(snippet) <= 2000


def test_normalize_title_unescapes_and_collapses_whitespace():
    assert normalize_title("  A &amp; B\n C ") == "A & B C"


async def test_tavily_provider_maps_results():
    captured: dict = {}

    def handler(request):
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Tavily <em>Doc</em>",
                        "url": "https://a.com/",
                        "content": "<p>Snippet</p>",
                        "score": 0.9,
                    },
                    {"title": "no url", "content": "skip"},
                ]
            },
        )

    provider = TavilyProvider(
        "key", transport=httpx.MockTransport(handler)
    )
    outcome = await provider.search(
        SearchRequest(query="q", max_results=5, domains=["a.com"], freshness="day")
    )
    assert captured["payload"]["api_key"] == "key"
    assert captured["payload"]["include_domains"] == ["a.com"]
    assert outcome.results[0].title == "Tavily Doc"
    assert outcome.results[0].snippet == "Snippet"
    assert outcome.results[0].source == "tavily"
    assert len(outcome.results) == 1
    assert any("freshness" in warning for warning in outcome.warnings)


async def test_tavily_provider_requires_api_key():
    provider = TavilyProvider("")
    with pytest.raises(ProviderNotConfigured):
        await provider.search(SearchRequest(query="q"))


async def test_tavily_provider_http_error_propagates():
    def handler(request):
        return httpx.Response(429, json={})

    provider = TavilyProvider("key", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await provider.search(SearchRequest(query="q"))


async def test_searxng_provider_requires_base_url():
    provider = SearxngProvider("")
    with pytest.raises(ProviderNotConfigured):
        await provider.search(SearchRequest(query="q"))


async def test_searxng_provider_maps_results_and_time_range(monkeypatch):
    captured: dict = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "SearXNG Doc",
                        "url": "https://s.com/",
                        "content": "snippet",
                        "publishedDate": "2026-01-02T00:00:00",
                    }
                ]
            },
        )

    async def allow_dns(parsed, **kwargs):
        return ["93.184.216.34"]

    monkeypatch.setattr(providers_module, "resolve_and_check", allow_dns)
    provider = SearxngProvider(
        "https://searxng.example",
        api_key="secret",
        transport=httpx.MockTransport(handler),
    )
    outcome = await provider.search(SearchRequest(query="q", freshness="week"))
    assert "format=json" in captured["url"]
    assert "time_range=week" in captured["url"]
    assert outcome.results[0].source == "searxng"
    assert outcome.results[0].published_at is not None


async def test_searxng_private_base_url_requires_trusted_host(monkeypatch):
    async def blocked_dns(parsed, **kwargs):
        raise WebAccessError("blocked_address", "private")

    monkeypatch.setattr(providers_module, "resolve_and_check", blocked_dns)
    provider = SearxngProvider(
        "http://10.0.0.5:8080", transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": []}))
    )
    with pytest.raises(WebAccessError) as excinfo:
        await provider.search(SearchRequest(query="q"))
    assert excinfo.value.code == "blocked_address"


async def test_searxng_trusted_host_allows_private_base_url(monkeypatch):
    async def allow_dns(parsed, **kwargs):
        return ["10.0.0.5"]

    monkeypatch.setattr(providers_module, "resolve_and_check", allow_dns)

    def handler(request):
        return httpx.Response(200, json={"results": []})

    provider = SearxngProvider(
        "http://searxng.internal:8080",
        transport=httpx.MockTransport(handler),
        trusted_hosts=frozenset({"searxng.internal"}),
    )
    outcome = await provider.search(SearchRequest(query="q"))
    assert outcome.provider == "searxng"


async def test_web_search_tool_wrapper_returns_structured_json(monkeypatch):
    provider = FakeProvider("fake", results=[_result("https://a.com/", title="A")])
    service = WebSearchService(_config(), providers={"fake": provider})
    from server.tools.api import web_search_tool

    monkeypatch.setattr(web_search_tool, "build_search_service", lambda: service)

    output = await web_search_tool.web_search.ainvoke({"query": "hello"})
    payload = json.loads(output)
    assert payload["provider"] == "fake"
    assert payload["results"][0]["url"] == "https://a.com/"


async def test_build_search_service_rejects_disabled_config(monkeypatch):
    import server.tools.web.config as web_config_module

    monkeypatch.setattr(
        web_config_module, "get_web_config", lambda: WebToolsConfig(enabled=False)
    )
    with pytest.raises(WebAccessError) as excinfo:
        search_module.build_search_service()
    assert excinfo.value.code == "disabled"
