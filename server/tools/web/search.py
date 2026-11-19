"""DeepSeek Search API client with Singleflight coalescing, dual-level cache, and circuit breaker."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import os
import re
import threading
from typing import Any, Protocol

import httpx

from core.outbound_network import OutboundNetworkPolicy
from core.tool_config import WebSearchConfig, WebToolsConfig
from server.tools.academic.runtime import ProviderCircuitBreaker, RedisAcademicStore
from server.tools.web.cache import TTLCache, cache_key
from server.tools.web.contracts import (
    UNTRUSTED_CONTENT_BANNER,
    TransientHttpError,
    WebAccessError,
    WebSearchInput,
    WebSearchResponse,
    WebSearchResultItem,
)
from server.tools.web.network_safety import check_literal_host, validate_url
from utils.logger import get_logger

logger = get_logger("nlp_agent.tools.web_search")

_WHITESPACE_RE = re.compile(r"\s+")
_SHARED_SEARCH_LOCK = threading.Lock()
_SHARED_SEARCH_SERVICE: WebSearchService | None = None


class WebSearchSharedCache(Protocol):
    """Minimal async Redis cache seam compatible with RedisAcademicStore."""

    async def get_cache(self, key: str) -> str | None: ...

    async def put_cache(self, key: str, value: str, ttl_s: int) -> None: ...


def _normalize_query_text(value: str | None) -> str:
    if not value:
        return ""
    return _WHITESPACE_RE.sub(" ", value).strip()


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _extract_error_detail(body_text: str) -> str:
    if not body_text:
        return ""
    try:
        payload = json.loads(body_text)
    except json.JSONDecodeError:
        return body_text.strip()[:200]
    if isinstance(payload, Mapping):
        err = payload.get("error")
        if isinstance(err, Mapping):
            msg = err.get("message") or err.get("code")
            if msg:
                return str(msg).strip()[:200]
        elif err:
            return str(err).strip()[:200]
    return body_text.strip()[:200]


def create_web_search_redis_store(
    config: WebSearchConfig,
) -> RedisAcademicStore | None:
    if not config.redis_enabled:
        return None
    redis_url = os.environ.get(config.redis_url_env, "").strip()
    if not redis_url:
        try:
            from configs.settings import settings

            gateway = settings.gateway_runtime
            if str(gateway.get("transport", "")).strip() == "redis":
                redis_url = str(gateway.get("redis_url", "")).strip()
        except Exception:
            redis_url = ""
    if not redis_url:
        return None
    from redis.asyncio import Redis

    return RedisAcademicStore(
        Redis.from_url(redis_url, decode_responses=True),
        key_prefix=config.redis_key_prefix,
        operation_timeout_s=config.redis_operation_timeout_s,
        unavailable_cooldown_s=config.circuit_cooldown_s,
    )


class WebSearchService:
    def __init__(
        self,
        config: WebToolsConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        cache: TTLCache | None = None,
        shared_cache: WebSearchSharedCache | None = None,
        circuit_breaker: ProviderCircuitBreaker | None = None,
        api_key: str | None = None,
    ) -> None:
        self.config = config
        self.transport = transport
        self.cache = (
            cache if cache is not None else TTLCache(config.search.cache_ttl_s)
        )
        self.shared_cache = shared_cache
        self._circuit = circuit_breaker or ProviderCircuitBreaker(
            failure_threshold=config.search.circuit_failure_threshold,
            cooldown_s=config.search.circuit_cooldown_s,
        )
        self._explicit_api_key = api_key
        self._cache_locks: dict[str, asyncio.Lock] = {}

    async def search(
        self,
        request: WebSearchInput,
        *,
        before_request: Callable[[], Awaitable[None]] | None = None,
        on_billable_success: Callable[[], Awaitable[None]] | None = None,
    ) -> WebSearchResponse:
        if not self.config.enabled or not self.config.search.enabled:
            raise WebAccessError("disabled", "web_search 工具已在配置中禁用")

        normalized_query = _normalize_query_text(request.query)
        normalized_ori = _normalize_query_text(request.ori_question)
        max_results = request.max_results
        key = cache_key(
            "web_search",
            normalized_query,
            normalized_ori,
            str(max_results),
        )

        cached = await self._read_cache(key)
        if cached is not None:
            return self._hydrate_cached(cached, request)

        lock = self._cache_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = await self._read_cache(key)
            if cached is not None:
                return self._hydrate_cached(cached, request)

            if not self._circuit.available():
                raise WebAccessError(
                    "circuit_open",
                    "DeepSeek Search API 连续异常已进入熔断冷却期，请稍后重试",
                )
            api_key = self._resolve_api_key()
            if not api_key:
                raise WebAccessError(
                    "missing_api_key",
                    f"未配置 {self.config.search.api_key_env}，无法执行联网搜索",
                )

            if before_request is not None:
                await before_request()

            raw_results = await self._post_search(
                query=request.query,
                ori_question=request.ori_question,
                max_results=max_results,
                api_key=api_key,
            )
            items, warnings = self._filter_and_rank(raw_results, max_results=max_results)
            response = WebSearchResponse(
                query=request.query,
                ori_question=request.ori_question,
                results=items,
                cache_hit=False,
                untrusted=True,
                banner=UNTRUSTED_CONTENT_BANNER,
                retrieved_at=datetime.now(timezone.utc),
                warnings=warnings,
            )
            await self._write_cache(key, response.model_dump_json())

            if items and on_billable_success is not None:
                await on_billable_success()

        logger.info(
            "web_search completed",
            raw_count=len(raw_results),
            returned_count=len(response.results),
            cache_hit=False,
        )
        return response

    def _hydrate_cached(
        self, raw_json: str, request: WebSearchInput
    ) -> WebSearchResponse:
        return WebSearchResponse.model_validate_json(raw_json).model_copy(
            update={
                "query": request.query,
                "ori_question": request.ori_question,
                "cache_hit": True,
            }
        )

    async def _read_cache(self, key: str) -> str | None:
        if self.config.search.cache_ttl_s <= 0:
            return None
        local = self.cache.get(key)
        if local is not None:
            logger.debug("web_search cache hit (memory)")
            return local
        if self.shared_cache is not None:
            try:
                shared = await self.shared_cache.get_cache(key)
                if shared is not None:
                    self.cache.put(key, shared)
                    logger.debug("web_search cache hit (redis)")
                    return shared
            except Exception as error:
                logger.warning(
                    "web_search redis cache read failed",
                    error_type=type(error).__name__,
                )
        return None

    async def _write_cache(self, key: str, serialized: str) -> None:
        ttl_s = self.config.search.cache_ttl_s
        if ttl_s <= 0:
            return
        self.cache.put(key, serialized)
        if self.shared_cache is not None:
            try:
                await self.shared_cache.put_cache(key, serialized, ttl_s)
            except Exception as error:
                logger.warning(
                    "web_search redis cache write failed",
                    error_type=type(error).__name__,
                )

    def _resolve_api_key(self) -> str:
        if self._explicit_api_key is not None:
            return self._explicit_api_key.strip()
        env_name = self.config.search.api_key_env
        if env_name in os.environ:
            return os.environ[env_name].strip()
        if self.transport is None:
            try:
                from configs.settings import auth_env_value

                return (auth_env_value(env_name, "") or "").strip()
            except Exception:
                return ""
        return ""

    def _proxy_url(self) -> str:
        configured = self.config.proxy_url.strip()
        if configured:
            return configured
        if self.config.proxy_url_env:
            return os.environ.get(self.config.proxy_url_env, "").strip()
        return ""

    def _build_client(self, base_url: str) -> httpx.AsyncClient:
        search_cfg = self.config.search
        network = self.config.network
        connect_timeout = min(network.connect_timeout_s, search_cfg.timeout_s)
        timeout = httpx.Timeout(
            connect=connect_timeout,
            read=search_cfg.timeout_s,
            write=connect_timeout,
            pool=connect_timeout,
        )
        policy = OutboundNetworkPolicy.from_environment(proxy_url=self._proxy_url())
        proxy = None if self.transport is not None else policy.proxy_for_url(base_url)
        return httpx.AsyncClient(
            transport=self.transport,
            proxy=proxy,
            timeout=timeout,
            headers={"User-Agent": self.config.user_agent},
            follow_redirects=False,
            trust_env=False,
        )

    async def _post_search(
        self,
        *,
        query: str,
        ori_question: str | None,
        max_results: int,
        api_key: str,
    ) -> list[Mapping[str, Any]]:
        search_cfg = self.config.search
        fetch_count = min(50, max(search_cfg.raw_fetch_count, max_results))
        payload: dict[str, Any] = {
            "query": query,
            "count": fetch_count,
            "need_content": False,
        }
        if ori_question:
            payload["ori_question"] = ori_question[:128]

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        async with self._build_client(search_cfg.base_url) as client:
            try:
                response = await client.post(
                    search_cfg.base_url,
                    json=payload,
                    headers=headers,
                )
            except httpx.TimeoutException as error:
                self._circuit.fail()
                raise TimeoutError(f"DeepSeek Search API 请求超时: {error}") from error
            except httpx.TransportError as error:
                self._circuit.fail()
                raise ConnectionError(
                    f"连接 DeepSeek Search API 失败: {error}"
                ) from error

        status = response.status_code
        if status == 429:
            retry_after = _parse_retry_after(response.headers.get("retry-after"))
            self._circuit.fail(cooldown_s=retry_after)
            raise TransientHttpError(429, "DeepSeek Search API 触发限流 (HTTP 429)")
        if status == 408 or status >= 500:
            self._circuit.fail()
            raise TransientHttpError(
                status,
                f"DeepSeek Search API 暂时不可用 (HTTP {status})",
            )
        if status == 401:
            raise WebAccessError(
                "provider_auth_failed",
                "DeepSeek Search API 鉴权失败 (HTTP 401)，请检查 API Key",
            )
        if status == 402:
            raise WebAccessError(
                "provider_quota_exhausted",
                "DeepSeek Search API 账户余额不足 (HTTP 402)",
            )
        if status >= 400:
            detail = _extract_error_detail(response.text)
            suffix = f": {detail}" if detail else ""
            raise WebAccessError(
                "http_error",
                f"DeepSeek Search API 返回 HTTP {status}{suffix}",
            )

        try:
            body = response.json()
        except ValueError as error:
            self._circuit.fail()
            raise WebAccessError(
                "invalid_response",
                "DeepSeek Search API 返回了无法解析的 JSON 响应",
            ) from error

        raw_results = body.get("results") if isinstance(body, Mapping) else None
        if not isinstance(raw_results, list):
            self._circuit.fail()
            raise WebAccessError(
                "invalid_response",
                "DeepSeek Search API 响应缺少有效的 results 数组",
            )

        self._circuit.succeed()
        return [item for item in raw_results if isinstance(item, Mapping)]

    def _filter_and_rank(
        self,
        raw_results: list[Mapping[str, Any]],
        *,
        max_results: int,
    ) -> tuple[list[WebSearchResultItem], list[str]]:
        search_cfg = self.config.search
        blocked_cidrs = tuple(self.config.network.blocked_cidrs)
        warnings: list[str] = []

        candidates: list[dict[str, Any]] = []
        for raw in raw_results:
            raw_url = str(raw.get("url") or "").strip()
            if not raw_url:
                continue
            try:
                parsed = validate_url(raw_url)
                check_literal_host(parsed, blocked_cidrs=blocked_cidrs)
            except WebAccessError:
                continue
            try:
                score = float(raw.get("rerank_score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            try:
                authority = int(raw.get("authority", -1))
            except (TypeError, ValueError):
                authority = -1
            site_name = raw.get("site_name")
            date_val = raw.get("date")
            candidates.append(
                {
                    "title": str(raw.get("title") or "").strip() or parsed.host,
                    "url": parsed.normalized,
                    "snippet": str(raw.get("snippet") or "").strip(),
                    "summary": str(raw.get("summary") or "").strip(),
                    "rerank_score": score,
                    "site_name": str(site_name).strip() if site_name else None,
                    "date": str(date_val).strip() if date_val else None,
                    "authority": authority,
                }
            )

        if not candidates:
            if raw_results:
                warnings.append("搜索结果未包含合法的公开 http(s) URL")
            return [], warnings

        candidates.sort(key=lambda item: item["rerank_score"], reverse=True)
        deduped: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for item in candidates:
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            deduped.append(item)

        above_threshold = [
            item
            for item in deduped
            if item["rerank_score"] >= search_cfg.min_rerank_score
        ]
        if above_threshold:
            selected = above_threshold[:max_results]
        else:
            fallback_count = min(2, max_results, len(deduped))
            selected = deduped[:fallback_count]
            warnings.append(
                f"所有结果重排分均低于阈值 {search_cfg.min_rerank_score}，"
                f"已保底保留前 {len(selected)} 条最高分结果"
            )

        items: list[WebSearchResultItem] = []
        for index, entry in enumerate(selected):
            keep_summary = index < search_cfg.top_summary_count
            summary = entry["summary"] if keep_summary else ""
            snippet = entry["snippet"]
            if not keep_summary and not snippet and entry["summary"]:
                snippet = entry["summary"][:200]
            items.append(
                WebSearchResultItem(
                    rank=index + 1,
                    title=entry["title"],
                    url=entry["url"],
                    snippet=snippet,
                    summary=summary,
                    rerank_score=entry["rerank_score"],
                    site_name=entry["site_name"],
                    date=entry["date"],
                    authority=entry["authority"],
                )
            )
        return items, warnings


def build_search_service(
    transport: httpx.AsyncBaseTransport | None = None,
) -> WebSearchService:
    from server.tools.web.config import get_web_config

    config = get_web_config()
    if not config.enabled or not config.search.enabled:
        raise WebAccessError("disabled", "web_search 工具已在配置中禁用")
    if transport is not None:
        return WebSearchService(config, transport=transport)
    global _SHARED_SEARCH_SERVICE
    with _SHARED_SEARCH_LOCK:
        if _SHARED_SEARCH_SERVICE is None or _SHARED_SEARCH_SERVICE.config != config:
            shared_cache = create_web_search_redis_store(config.search)
            _SHARED_SEARCH_SERVICE = WebSearchService(
                config,
                shared_cache=shared_cache,
            )
        return _SHARED_SEARCH_SERVICE
