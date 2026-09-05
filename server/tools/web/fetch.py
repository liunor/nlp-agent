"""Read-only HTTP fetch pipeline: per-hop SSRF checks, size caps, extraction."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import httpx

from core.tool_config import WebToolsConfig
from server.tools.web.cache import TTLCache, cache_key
from server.tools.web.contracts import (
    UNTRUSTED_CONTENT_BANNER,
    Citation,
    TransientHttpError,
    WebAccessError,
    WebFetchInput,
    WebFetchResponse,
)
from server.tools.web.extractors import extract_html, extract_json, extract_text
from server.tools.web.network_safety import (
    ParsedUrl,
    resolve_and_check,
    validate_url,
)
from utils.logger import get_logger


logger = get_logger("nlp_agent.tools.web_fetch")

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_SHARED_SERVICE_LOCK = threading.Lock()
_SHARED_SERVICE: WebFetchService | None = None


def _host_digest(host: str) -> str:
    return hashlib.sha256(host.encode("utf-8")).hexdigest()[:16]


def _parse_charset(content_type: str) -> str | None:
    for param in content_type.split(";")[1:]:
        key, _, value = param.partition("=")
        if key.strip().lower() == "charset":
            return value.strip().strip('"').strip("'") or None
    return None


def _decode_body(body: bytes, charset: str | None) -> str:
    encoding = charset or "utf-8"
    try:
        return body.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace")


class WebFetchService:
    def __init__(
        self,
        config: WebToolsConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        cache: TTLCache | None = None,
    ) -> None:
        self.config = config
        self.transport = transport
        self.cache = cache if cache is not None else TTLCache(config.fetch.cache_ttl_s)
        self._cache_locks: dict[str, asyncio.Lock] = {}

    async def fetch(
        self,
        request: WebFetchInput,
        *,
        before_download: Callable[[], Awaitable[None]] | None = None,
    ) -> WebFetchResponse:
        entry = validate_url(request.url)
        max_chars = min(request.max_chars, self.config.fetch.max_chars)
        key = cache_key(
            "fetch", entry.normalized, request.extract_mode, str(max_chars)
        )
        cached = self.cache.get(key)
        if cached is not None:
            logger.debug("web_fetch cache hit", host_digest=_host_digest(entry.host))
            return WebFetchResponse.model_validate_json(cached).model_copy(
                update={"cache_hit": True}
            )
        lock = self._cache_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self.cache.get(key)
            if cached is not None:
                logger.debug("web_fetch cache hit", host_digest=_host_digest(entry.host))
                return WebFetchResponse.model_validate_json(cached).model_copy(
                    update={"cache_hit": True}
                )
            if before_download is not None:
                await before_download()
            download = await self._download(
                entry, as_markdown=request.extract_mode == "markdown"
            )
            truncated, text_body, warnings = self._extract(download, max_chars)
            result = WebFetchResponse(
                url=entry.normalized,
                final_url=download["final_url"],
                title=download["title"],
                status_code=download["status_code"],
                content_type=download["content_type"],
                extractor=download["extractor"],
                text=f"{UNTRUSTED_CONTENT_BANNER}\n\n{text_body}",
                truncated=truncated,
                untrusted=True,
                citation=Citation(
                    title=download["title"],
                    url=download["final_url"],
                    retrieved_at=datetime.now(timezone.utc),
                    source_provider="web_fetch",
                ),
                warnings=warnings,
            )
            self.cache.put(key, result.model_dump_json())
        logger.info(
            "web_fetch completed",
            host_digest=_host_digest(entry.host),
            http_status=result.status_code,
            redirect_count=download["redirect_count"],
            extractor=result.extractor,
            truncated=result.truncated,
            response_bytes=download["response_bytes"],
        )
        return result

    def _build_client(self) -> httpx.AsyncClient:
        network = self.config.network
        timeout = httpx.Timeout(
            connect=network.connect_timeout_s,
            read=network.read_timeout_s,
            write=network.connect_timeout_s,
            pool=network.connect_timeout_s,
        )
        proxy = None if self.transport is not None else (self.config.proxy_url or None)
        return httpx.AsyncClient(
            transport=self.transport,
            proxy=proxy,
            timeout=timeout,
            headers={"User-Agent": self.config.user_agent},
            follow_redirects=False,
        )

    async def _download(self, entry: ParsedUrl, *, as_markdown: bool) -> dict[str, Any]:
        network = self.config.network
        blocked_cidrs = tuple(network.blocked_cidrs)
        current = entry
        redirects = 0
        async with self._build_client() as client:
            while True:
                await resolve_and_check(current, blocked_cidrs=blocked_cidrs)
                client.cookies.clear()
                try:
                    response = await client.send(
                        client.build_request("GET", current.normalized), stream=True
                    )
                except httpx.TimeoutException as error:
                    raise TimeoutError(f"读取 {current.host} 超时: {error}") from error
                except httpx.TransportError as error:
                    raise ConnectionError(
                        f"连接 {current.host} 失败: {error}"
                    ) from error
                try:
                    if (
                        response.status_code in _REDIRECT_STATUSES
                        and response.headers.get("location")
                    ):
                        redirects += 1
                        if redirects > network.max_redirects:
                            raise WebAccessError(
                                "too_many_redirects",
                                f"重定向超过 {network.max_redirects} 跳",
                            )
                        location = response.headers["location"]
                        next_url = str(response.url.join(location))
                        current = validate_url(next_url)
                        continue
                    if response.status_code == 429:
                        raise TransientHttpError(
                            429, f"HTTP 429 rate limit from {current.host}"
                        )
                    if response.status_code >= 500:
                        raise TransientHttpError(
                            response.status_code,
                            f"HTTP {response.status_code} temporarily unavailable "
                            f"from {current.host}",
                        )
                    if response.status_code >= 400:
                        raise WebAccessError(
                            "http_error", f"来源返回 HTTP {response.status_code}"
                        )
                    content_type = response.headers.get("content-type", "")
                    main_type = content_type.split(";")[0].strip().lower()
                    if main_type not in self.config.fetch.allowed_content_types:
                        raise WebAccessError(
                            "unsupported_content_type",
                            f"不支持的内容类型 {main_type or '未知'}",
                        )
                    body, overflow = await self._read_limited(response)
                    if overflow:
                        raise WebAccessError(
                            "response_too_large",
                            f"响应超过 {network.max_response_bytes} 字节上限",
                        )
                finally:
                    await response.aclose()
                text = _decode_body(body, _parse_charset(content_type))
                extracted = self._extract_body(text, main_type, as_markdown)
                return {
                    "final_url": current.normalized,
                    "status_code": response.status_code,
                    "content_type": main_type,
                    "title": extracted.title,
                    "extractor": extracted.extractor,
                    "text": extracted.text,
                    "warnings": list(extracted.warnings),
                    "redirect_count": redirects,
                    "response_bytes": len(body),
                }

    async def _read_limited(self, response: httpx.Response) -> tuple[bytes, bool]:
        limit = self.config.network.max_response_bytes
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > limit:
                return b"", True
            chunks.append(chunk)
        return b"".join(chunks), False

    @staticmethod
    def _extract_body(text: str, main_type: str, as_markdown: bool):
        if main_type == "text/html":
            return extract_html(text, as_markdown=as_markdown)
        if main_type == "application/json":
            return extract_json(text)
        return extract_text(text)

    @staticmethod
    def _extract(
        download: dict[str, Any], max_chars: int
    ) -> tuple[bool, str, list[str]]:
        warnings = list(download["warnings"])
        body = download["text"]
        truncated = len(body) > max_chars
        if truncated:
            body = body[:max_chars]
            warnings.append(f"正文超过 {max_chars} 字符，已截断")
        return truncated, body, warnings


def build_fetch_service(
    transport: httpx.AsyncBaseTransport | None = None,
) -> WebFetchService:
    from server.tools.web.config import get_web_config

    config = get_web_config()
    if not config.enabled:
        raise WebAccessError("disabled", "web 工具已在配置中禁用")
    if transport is not None:
        return WebFetchService(config, transport=transport)
    global _SHARED_SERVICE
    with _SHARED_SERVICE_LOCK:
        if _SHARED_SERVICE is None or _SHARED_SERVICE.config != config:
            _SHARED_SERVICE = WebFetchService(config)
        return _SHARED_SERVICE
