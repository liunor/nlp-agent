"""Web search service: provider selection, fallback, normalization, and dedupe."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx

from configs.settings import settings
from core.tool_config import WebToolsConfig
from server.tools.web.cache import TTLCache, cache_key
from server.tools.web.contracts import (
    SearchProvider,
    SearchRequest,
    SearchResult,
    TransientHttpError,
    WebAccessError,
    WebSearchInput,
    WebSearchResponse,
)
from server.tools.web.providers import (
    ProviderNotConfigured,
    SearxngProvider,
    TavilyProvider,
)
from utils.logger import get_logger


logger = get_logger("nlp_agent.tools.web_search")


def _canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    canonical = f"{parts.scheme.lower()}://{host}{path}"
    if parts.query:
        canonical += f"?{parts.query}"
    return canonical


class WebSearchService:
    def __init__(
        self,
        config: WebToolsConfig,
        *,
        providers: Mapping[str, SearchProvider] | None = None,
        cache: TTLCache | None = None,
    ) -> None:
        self.config = config
        self.providers = (
            dict(providers) if providers is not None else self._build_default_providers()
        )
        self.cache = cache if cache is not None else TTLCache(config.search.cache_ttl_s)

    def _build_default_providers(self) -> dict[str, SearchProvider]:
        search = self.config.search
        providers: dict[str, SearchProvider] = {}
        for provider_id, spec in search.providers.items():
            if not spec.enabled:
                continue
            if provider_id == TavilyProvider.id:
                env_name = spec.api_key_env or "TAVILY_API_KEY"
                providers[provider_id] = TavilyProvider(
                    api_key=getattr(settings, env_name, ""),
                    timeout_s=spec.timeout_s,
                )
            elif provider_id == SearxngProvider.id:
                base_url = (
                    getattr(settings, spec.base_url_env, "") if spec.base_url_env else ""
                )
                api_key = (
                    getattr(settings, spec.api_key_env, "") if spec.api_key_env else ""
                )
                providers[provider_id] = SearxngProvider(
                    base_url=base_url,
                    api_key=api_key,
                    timeout_s=spec.timeout_s,
                    blocked_cidrs=tuple(self.config.network.blocked_cidrs),
                    trusted_hosts=frozenset(self.config.trusted_service_hosts),
                )
            else:
                logger.warning("unknown search provider in config", provider=provider_id)
        return providers

    def _select_provider_ids(
        self, requested: str | None, warnings: list[str]
    ) -> list[str]:
        search = self.config.search
        ordered = [search.default_provider] + [
            item for item in search.fallback_providers if item != search.default_provider
        ]
        if requested:
            if not self.config.allow_provider_override:
                warnings.append(
                    f"配置不允许覆盖搜索提供者，已忽略 provider={requested}"
                )
            else:
                spec = search.providers.get(requested)
                if spec is None or not spec.enabled:
                    warnings.append(
                        f"provider={requested} 未在配置中启用，回退到默认顺序"
                    )
                else:
                    return [requested] + [item for item in ordered if item != requested]
        return ordered

    async def search(self, request: WebSearchInput) -> WebSearchResponse:
        key = cache_key(
            "search",
            request.query.strip().lower(),
            str(request.max_results),
            ",".join(sorted(domain.lower() for domain in request.domains)),
            request.freshness or "",
        )
        cached = self.cache.get(key)
        if cached is not None:
            return WebSearchResponse.model_validate_json(cached)

        warnings: list[str] = []
        provider_ids = self._select_provider_ids(request.provider, warnings)
        search_request = SearchRequest(
            query=request.query,
            max_results=request.max_results,
            domains=list(request.domains),
            freshness=request.freshness,
        )
        last_transient: Exception | None = None
        for provider_id in provider_ids:
            provider = self.providers.get(provider_id)
            if provider is None:
                warnings.append(f"provider {provider_id} 未配置，已跳过")
                continue
            try:
                outcome = await provider.search(search_request)
            except ProviderNotConfigured as error:
                warnings.append(error.message)
                continue
            except httpx.HTTPStatusError as error:
                status = error.response.status_code
                warnings.append(
                    f"provider {provider_id} 返回 HTTP {status}，尝试下一个来源"
                )
                if status == 429:
                    message = f"provider {provider_id} HTTP 429 rate limit"
                else:
                    message = (
                        f"provider {provider_id} HTTP {status} "
                        f"temporarily unavailable"
                    )
                last_transient = TransientHttpError(status, message)
                continue
            except httpx.HTTPError as error:
                warnings.append(f"provider {provider_id} 请求失败，尝试下一个来源")
                last_transient = ConnectionError(
                    f"provider {provider_id} 请求失败: {error}"
                )
                continue
            results = self._normalize(outcome.results, request.max_results)
            response = WebSearchResponse(
                query=request.query,
                provider=provider_id,
                results=results,
                total_results=len(results),
                warnings=warnings + outcome.warnings,
            )
            self.cache.put(key, response.model_dump_json())
            logger.info(
                "web_search completed",
                provider=provider_id,
                result_count=len(results),
            )
            return response
        if last_transient is not None:
            raise last_transient
        raise ProviderNotConfigured(
            "search", "没有可用的搜索提供者，请检查 tools.web.search 配置"
        )

    @staticmethod
    def _normalize(
        results: list[SearchResult], max_results: int
    ) -> list[SearchResult]:
        seen: set[str] = set()
        normalized: list[SearchResult] = []
        for item in results:
            url = item.url.strip()
            if not url:
                continue
            canonical = _canonical_url(url)
            if canonical in seen:
                continue
            seen.add(canonical)
            normalized.append(item.model_copy(update={"url": url}))
            if len(normalized) >= max_results:
                break
        return normalized


def build_search_service() -> WebSearchService:
    from server.tools.web.config import get_web_config

    config = get_web_config()
    if not config.enabled:
        raise WebAccessError("disabled", "web 工具已在配置中禁用")
    return WebSearchService(config)
