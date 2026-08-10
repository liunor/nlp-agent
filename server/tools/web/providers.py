"""Search provider adapters normalizing external results into SearchResult."""

from __future__ import annotations

import html as html_lib
import re
from collections.abc import Sequence
from datetime import datetime

import httpx

from core.tool_config import DEFAULT_BLOCKED_CIDRS
from server.tools.web.contracts import (
    SNIPPET_MAX_CHARS,
    ProviderSearchResult,
    SearchRequest,
    SearchResult,
    WebAccessError,
)
from server.tools.web.network_safety import resolve_and_check, validate_url


class ProviderNotConfigured(WebAccessError):
    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        super().__init__("provider_unconfigured", f"{provider}: {message}")


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_snippet(raw: str) -> str:
    text = _TAG_RE.sub(" ", html_lib.unescape(raw or ""))
    text = _WS_RE.sub(" ", text).strip()
    return text[:SNIPPET_MAX_CHARS]


def normalize_title(raw: str) -> str:
    text = _TAG_RE.sub(" ", html_lib.unescape(raw or ""))
    return _WS_RE.sub(" ", text).strip()


def _parse_published_at(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


class TavilyProvider:
    id = "tavily"
    BASE_URL = "https://api.tavily.com/search"

    def __init__(
        self,
        api_key: str,
        *,
        timeout_s: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.transport = transport

    async def search(self, request: SearchRequest) -> ProviderSearchResult:
        if not self.api_key:
            raise ProviderNotConfigured(self.id, "TAVILY_API_KEY 未配置")
        payload: dict = {
            "api_key": self.api_key,
            "query": request.query,
            "max_results": request.max_results,
            "search_depth": "basic",
            "include_answer": False,
        }
        if request.domains:
            payload["include_domains"] = list(request.domains)
        warnings: list[str] = []
        if request.freshness:
            warnings.append("tavily 不支持 freshness 过滤，已忽略该条件")
        async with httpx.AsyncClient(
            timeout=self.timeout_s, transport=self.transport
        ) as client:
            response = await client.post(self.BASE_URL, json=payload)
            response.raise_for_status()
        items = response.json().get("results", [])
        results = [
            SearchResult(
                title=normalize_title(item.get("title", "")),
                url=str(item.get("url", "")).strip(),
                snippet=clean_snippet(item.get("content", "")),
                score=item.get("score"),
                source=self.id,
            )
            for item in items
            if item.get("url")
        ]
        return ProviderSearchResult(
            provider=self.id,
            results=results,
            total_results=len(results),
            warnings=warnings,
        )


class SearxngProvider:
    id = "searxng"

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        timeout_s: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
        blocked_cidrs: Sequence[str] = DEFAULT_BLOCKED_CIDRS,
        trusted_hosts: frozenset[str] = frozenset(),
    ) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.transport = transport
        self.blocked_cidrs = tuple(blocked_cidrs)
        self.trusted_hosts = frozenset(trusted_hosts)

    async def search(self, request: SearchRequest) -> ProviderSearchResult:
        if not self.base_url:
            raise ProviderNotConfigured(self.id, "NLP_AGENT_SEARXNG_BASE_URL 未配置")
        parsed = validate_url(self.base_url, allowed_ports=frozenset())
        await resolve_and_check(
            parsed,
            blocked_cidrs=self.blocked_cidrs,
            trusted_hosts=self.trusted_hosts,
        )
        params: dict[str, str] = {
            "q": request.query,
            "format": "json",
            "safesearch": "1",
        }
        if request.freshness:
            params["time_range"] = request.freshness
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(
            timeout=self.timeout_s, transport=self.transport
        ) as client:
            response = await client.get(
                f"{self.base_url}/search", params=params, headers=headers
            )
            response.raise_for_status()
        items = response.json().get("results", [])
        results: list[SearchResult] = []
        for item in items:
            url = str(item.get("url", "")).strip()
            if not url:
                continue
            results.append(
                SearchResult(
                    title=normalize_title(item.get("title", "")),
                    url=url,
                    snippet=clean_snippet(item.get("content", "")),
                    published_at=_parse_published_at(
                        item.get("publishedDate") or item.get("published_date")
                    ),
                    source=self.id,
                )
            )
        return ProviderSearchResult(
            provider=self.id, results=results, total_results=len(results)
        )
