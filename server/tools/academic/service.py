"""Academic search aggregation service managing providers, caching, and source status."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from difflib import SequenceMatcher
import re
import time
from typing import Sequence

from pydantic import HttpUrl

from core.tool_config import AcademicToolsConfig, load_agent_runtime_config
from server.tools.academic.arxiv import ArxivProvider
from server.tools.academic.contracts import (
    AcademicPaper,
    AcademicSearchInput,
    AcademicSearchResponse,
    AcademicSourceStatus,
)
from server.tools.academic.normalize import (
    deduplicate_and_merge_papers,
    google_scholar_verification_url,
    merge_papers,
    normalize_title,
)
from server.tools.academic.provider import (
    AcademicCircuitOpenError,
    AcademicProvider,
    AcademicProviderError,
    AcademicRateLimitError,
    AcademicTimeoutError,
)
from server.tools.academic.runtime import (
    AcademicMetrics,
    AcademicSharedStore,
    ProviderCircuitBreaker,
    create_academic_redis_store,
    global_academic_metrics,
)
from server.tools.academic.semantic_scholar import SemanticScholarProvider
from server.tools.web.cache import TTLCache, cache_key
from utils.logger import get_logger

logger = get_logger("nlp_agent.academic_search")

_GLOBAL_ACADEMIC_SERVICE: AcademicSearchService | None = None


def _academic_relevance_score(paper: AcademicPaper, query: str) -> float:
    """Combine local title relevance, provider rank, corroboration, and metadata."""
    normalized_query = normalize_title(query)
    normalized_title = normalize_title(paper.title)
    query_tokens = set(re.findall(r"\w+", normalized_query, flags=re.UNICODE))
    title_tokens = set(re.findall(r"\w+", normalized_title, flags=re.UNICODE))

    exact_score = 1000.0 if normalized_query == normalized_title else 0.0
    containment_score = (
        200.0
        if normalized_query
        and normalized_title
        and (
            normalized_query in normalized_title or normalized_title in normalized_query
        )
        else 0.0
    )
    token_overlap = (
        len(query_tokens & title_tokens) / len(query_tokens) if query_tokens else 0.0
    )
    similarity = SequenceMatcher(None, normalized_query, normalized_title).ratio()

    ranks = [record.rank for record in paper.source_records if record.rank is not None]
    provider_rank_score = 20.0 / min(ranks) if ranks else 0.0
    corroboration_score = max(0, len(paper.source_records) - 1) * 15.0
    identifier_score = sum(
        2.0
        for identifier in (
            paper.identifiers.doi,
            paper.identifiers.arxiv_id,
            paper.identifiers.acl_id,
            paper.identifiers.semantic_scholar_id,
        )
        if identifier
    )

    return (
        exact_score
        + containment_score
        + token_overlap * 120.0
        + similarity * 80.0
        + provider_rank_score
        + corroboration_score
        + identifier_score
    )


class AcademicSearchService:
    """Orchestrates academic providers with TTL caching and partial failure isolation."""

    def __init__(
        self,
        config: AcademicToolsConfig | None = None,
        *,
        providers: Sequence[AcademicProvider] | None = None,
        cache: TTLCache | None = None,
        shared_store: AcademicSharedStore | None = None,
        metrics: AcademicMetrics | None = None,
    ) -> None:
        self.config = config or AcademicToolsConfig()
        self.cache = cache or TTLCache(self.config.search_cache_ttl_s)
        self.shared_store = shared_store
        self.metrics = metrics or global_academic_metrics
        reliability = self.config.reliability
        if providers is not None:
            self.providers = list(providers)
        else:
            self.providers = []
            if self.config.arxiv.enabled:
                self.providers.append(
                    ArxivProvider(self.config.arxiv, shared_store=shared_store)
                )
            if self.config.semantic_scholar.enabled:
                self.providers.append(
                    SemanticScholarProvider(self.config.semantic_scholar)
                )
        self._circuits = {
            provider.name: ProviderCircuitBreaker(
                failure_threshold=reliability.circuit_failure_threshold,
                cooldown_s=reliability.circuit_cooldown_s,
            )
            for provider in self.providers
        }

    def _active_providers(self) -> list[AcademicProvider]:
        return [
            p for p in self.providers
            if getattr(self.config, p.name, None) is None
            or getattr(self.config, p.name).enabled
        ]

    def _provider_fingerprint(self) -> str:
        # Version both caches together; include configuration but never API keys.
        return cache_key(
            "academic-v2",
            ",".join(sorted(p.name for p in self._active_providers())),
            self.config.model_dump_json(),
        )

    def _get_cache_key(self, request: AcademicSearchInput, query_used: str) -> str:
        sources_str = ",".join(sorted(request.sources))
        return cache_key(
            "academic_search",
            self._provider_fingerprint(),
            normalize_title(query_used),
            sources_str,
            str(request.year_from or ""),
            str(request.year_to or ""),
            request.sort,
            str(request.max_results),
        )

    async def search(self, request: AcademicSearchInput) -> AcademicSearchResponse:
        query_used = (request.query_en or request.query).strip()
        # The request schema protects the public API at ten results, while the
        # runtime configuration provides the deployment-wide upper bound.
        effective_max_results = min(request.max_results, self.config.max_results)
        provider_request = request.model_copy(
            update={"max_results": effective_max_results}
        )
        now = datetime.now(timezone.utc)

        if not self.config.enabled:
            disabled_sources = list(dict.fromkeys(request.sources)) or ["arxiv"]
            return AcademicSearchResponse(
                query=request.query,
                query_used=query_used,
                papers=[],
                source_status=[
                    AcademicSourceStatus(
                        source=source,
                        status="skipped",
                        message="academic search disabled by configuration",
                    )
                    for source in disabled_sources
                ],
                partial=False,
                warnings=["academic search is disabled"],
                retrieved_at=now,
            )

        # Prefer Redis, then fall back to the existing process-local cache.
        ckey = self._get_cache_key(provider_request, query_used)
        cached_raw: str | None = None
        cache_backend = "miss"
        if self.shared_store is not None:
            try:
                cached_raw = await self.shared_store.get_cache(ckey)
                if cached_raw is not None:
                    cache_backend = "redis"
            except Exception as error:
                logger.warning(
                    "academic.cache.redis_read_failed",
                    error_type=type(error).__name__,
                )
        if cached_raw is None:
            cached_raw = self.cache.get(ckey)
            if cached_raw is not None:
                cache_backend = "memory"
        if cached_raw:
            try:
                cached_response = AcademicSearchResponse.model_validate_json(cached_raw)
                available = {p.name for p in self._active_providers()}
                allowed = available & set(request.sources) if request.sources else available
                if (
                    set(request.sources) - available
                    or any(s.source not in allowed for s in cached_response.source_status)
                    or any(
                        r.source not in allowed
                        for paper in cached_response.papers for r in paper.source_records
                    )
                ):
                    raise ValueError("cached sources are no longer available")
                logger.info(
                    "academic.search.cache_hit",
                    cache_backend=cache_backend,
                    paper_count=len(cached_response.papers),
                    partial=cached_response.partial,
                )
                cached_response = cached_response.model_copy(
                    update={
                        # The normalized query determines result reuse, but the
                        # response must still describe the current user input.
                        "query": request.query,
                        "query_used": query_used,
                        "papers": cached_response.papers[:effective_max_results],
                    }
                )
                await self._record_search_metrics(
                    cache_hit=True, partial=cached_response.partial
                )
                return cached_response
            except Exception:
                logger.warning(
                    "academic.cache.invalid_entry", cache_backend=cache_backend
                )

        logger.info("academic.search.cache_miss")

        raw_papers: list[AcademicPaper] = []
        statuses: list[AcademicSourceStatus] = []
        warnings: list[str] = []
        has_failure = False

        # Filter providers by requested sources if specified
        requested_sources = set(request.sources)
        if requested_sources:
            available_sources = {p.name for p in self._active_providers()}
            target_providers = [
                p for p in self._active_providers() if p.name in requested_sources
            ]
            unsupported_sources = sorted(requested_sources - available_sources)
            for s in unsupported_sources:
                has_failure = True
                statuses.append(
                    AcademicSourceStatus(
                        source=s,
                        status="skipped",
                        message=f"source '{s}' is not available in current configuration",
                    )
                )
                warnings.append(f"source {s} is not available in current configuration")
        else:
            target_providers = self._active_providers()

        # Concurrently execute targeted providers
        async def _query_provider(
            provider: AcademicProvider,
        ) -> tuple[AcademicProvider, list[AcademicPaper] | Exception]:
            circuit = self._circuits[provider.name]
            if not circuit.available():
                error = AcademicCircuitOpenError(
                    f"{provider.name} circuit is temporarily open"
                )
                await self._record_provider_metrics(
                    provider.name, status="circuit_open", latency_ms=0
                )
                return provider, error

            reliability = self.config.reliability
            last_error: Exception | None = None
            for attempt in range(1, reliability.retry_attempts + 1):
                started = time.monotonic()
                try:
                    result = await provider.search(provider_request)
                    latency_ms = int((time.monotonic() - started) * 1000)
                    circuit.succeed()
                    await self._record_provider_metrics(
                        provider.name, status="ok", latency_ms=latency_ms
                    )
                    return provider, result
                except Exception as exc:
                    latency_ms = int((time.monotonic() - started) * 1000)
                    last_error = exc
                    status = self._provider_error_status(exc)
                    cooldown = (
                        exc.retry_after_s
                        if isinstance(exc, AcademicRateLimitError)
                        else None
                    )
                    circuit.fail(cooldown_s=cooldown)
                    await self._record_provider_metrics(
                        provider.name, status=status, latency_ms=latency_ms
                    )
                    retryable = isinstance(
                        exc, (AcademicTimeoutError, AcademicProviderError)
                    ) and not isinstance(exc, AcademicRateLimitError)
                    if (
                        attempt >= reliability.retry_attempts
                        or not retryable
                        or not circuit.available()
                    ):
                        break
                    delay = min(
                        reliability.retry_max_delay_s,
                        reliability.retry_base_delay_s * (2 ** (attempt - 1)),
                    )
                    logger.warning(
                        "academic.provider.retry",
                        provider=provider.name,
                        attempt=attempt,
                        delay_s=delay,
                        error_type=type(exc).__name__,
                    )
                    if delay > 0:
                        await asyncio.sleep(delay)
            assert last_error is not None
            return provider, last_error

        if target_providers:
            query_tasks = [_query_provider(p) for p in target_providers]
            execution_results = await asyncio.gather(*query_tasks)

            for provider, result in execution_results:
                if isinstance(result, list):
                    statuses.append(
                        AcademicSourceStatus(
                            source=provider.name,  # type: ignore[arg-type]
                            status="ok",
                        )
                    )
                    raw_papers.extend(result)
                elif isinstance(result, AcademicTimeoutError):
                    has_failure = True
                    statuses.append(
                        AcademicSourceStatus(
                            source=provider.name,  # type: ignore[arg-type]
                            status="timeout",
                            message=str(result),
                        )
                    )
                    warnings.append(f"source {provider.name} timed out")
                elif isinstance(result, AcademicRateLimitError):
                    has_failure = True
                    statuses.append(
                        AcademicSourceStatus(
                            source=provider.name,  # type: ignore[arg-type]
                            status="rate_limited",
                            message=str(result),
                        )
                    )
                    warnings.append(f"source {provider.name} rate limited")
                elif isinstance(result, AcademicCircuitOpenError):
                    has_failure = True
                    statuses.append(
                        AcademicSourceStatus(
                            source=provider.name,  # type: ignore[arg-type]
                            status="circuit_open",
                            message=str(result),
                        )
                    )
                    warnings.append(f"source {provider.name} circuit open")
                elif isinstance(result, AcademicProviderError):
                    has_failure = True
                    statuses.append(
                        AcademicSourceStatus(
                            source=provider.name,  # type: ignore[arg-type]
                            status="error",
                            message=str(result),
                        )
                    )
                    warnings.append(f"source {provider.name} error: {result}")
                else:
                    has_failure = True
                    statuses.append(
                        AcademicSourceStatus(
                            source=provider.name,  # type: ignore[arg-type]
                            status="error",
                            message=f"unexpected error: {result}",
                        )
                    )
                    warnings.append(f"source {provider.name} failed unexpectedly")

        raw_papers, metadata_warnings = await self._hydrate_cached_metadata(
            raw_papers, allowed_sources={p.name for p in target_providers}
        )
        warnings.extend(metadata_warnings)

        # Deduplicate and merge papers across sources
        deduped_papers, merge_warnings = deduplicate_and_merge_papers(raw_papers)
        warnings.extend(merge_warnings)

        # Post-filter by year if requested
        if request.year_from or request.year_to:
            filtered_papers = []
            for paper in deduped_papers:
                year = None
                if paper.published_at:
                    year = paper.published_at.year
                elif paper.first_submitted_at:
                    year = paper.first_submitted_at.year
                elif paper.publication_year is not None:
                    year = paper.publication_year

                if year is not None:
                    if request.year_from and year < request.year_from:
                        continue
                    if request.year_to and year > request.year_to:
                        continue
                filtered_papers.append(paper)
            deduped_papers = filtered_papers

        # Sort papers
        if request.sort == "recent":

            def _recent_key(p: AcademicPaper):
                dt = p.published_at or p.first_submitted_at
                if dt is None and p.publication_year is not None:
                    dt = datetime(p.publication_year, 1, 1, tzinfo=timezone.utc)
                return (
                    dt if dt is not None else datetime.min.replace(tzinfo=timezone.utc)
                )

            deduped_papers.sort(key=_recent_key, reverse=True)
        else:
            deduped_papers.sort(
                key=lambda paper: _academic_relevance_score(paper, query_used),
                reverse=True,
            )

        # Slice to the request limit after applying the deployment-wide cap.
        papers = deduped_papers[:effective_max_results]
        papers = [
            paper.model_copy(
                update={
                    "scholar_url": HttpUrl(
                        google_scholar_verification_url(paper.title)
                    )
                }
            )
            for paper in papers
        ]

        partial = has_failure and (
            len(papers) > 0 or len(target_providers) > 1 or len(self.providers) > 1
        )
        if not papers and has_failure:
            partial = True

        response = AcademicSearchResponse(
            query=request.query,
            query_used=query_used,
            papers=papers,
            source_status=statuses,
            partial=partial,
            warnings=warnings,
            retrieved_at=now,
        )

        # Partial responses are deliberately not cached so provider recovery is
        # visible on the next request.
        if not has_failure:
            serialized = response.model_dump_json()
            self.cache.put(ckey, serialized)
            if self.shared_store is not None:
                try:
                    await self.shared_store.put_cache(
                        ckey, serialized, self.config.search_cache_ttl_s
                    )
                except Exception as error:
                    logger.warning(
                        "academic.cache.redis_write_failed",
                        error_type=type(error).__name__,
                    )
        await self._store_metadata(papers)

        await self._record_search_metrics(cache_hit=False, partial=partial)
        logger.info(
            "academic.search.completed",
            paper_count=len(papers),
            partial=partial,
            cache_hit=False,
            source_status={item.source: item.status for item in statuses},
        )

        return response

    @staticmethod
    def _provider_error_status(error: Exception) -> str:
        if isinstance(error, AcademicTimeoutError):
            return "timeout"
        if isinstance(error, AcademicRateLimitError):
            return "rate_limited"
        if isinstance(error, AcademicCircuitOpenError):
            return "circuit_open"
        return "error"

    async def _record_provider_metrics(
        self, provider: str, *, status: str, latency_ms: int
    ) -> None:
        self.metrics.record_provider(provider, status=status, latency_ms=latency_ms)
        logger.info(
            "academic.provider.completed",
            provider=provider,
            status=status,
            latency_ms=latency_ms,
        )
        if self.shared_store is not None:
            try:
                await self.shared_store.record_provider(
                    provider, status=status, latency_ms=latency_ms
                )
            except Exception as error:
                logger.warning(
                    "academic.metrics.redis_provider_write_failed",
                    provider=provider,
                    error_type=type(error).__name__,
                )

    async def _record_search_metrics(self, *, cache_hit: bool, partial: bool) -> None:
        self.metrics.record_search(cache_hit=cache_hit, partial=partial)
        if self.shared_store is not None:
            try:
                await self.shared_store.record_search(
                    cache_hit=cache_hit, partial=partial
                )
            except Exception as error:
                logger.warning(
                    "academic.metrics.redis_search_write_failed",
                    error_type=type(error).__name__,
                )

    def _paper_metadata_keys(self, paper: AcademicPaper) -> tuple[str, ...]:
        identifiers = paper.identifiers
        raw_keys = [
            f"doi:{identifiers.doi}" if identifiers.doi else None,
            f"arxiv:{identifiers.arxiv_id}" if identifiers.arxiv_id else None,
            f"acl:{identifiers.acl_id}" if identifiers.acl_id else None,
            (
                f"semantic_scholar:{identifiers.semantic_scholar_id}"
                if identifiers.semantic_scholar_id
                else None
            ),
            f"corpus:{identifiers.corpus_id}" if identifiers.corpus_id else None,
            f"record:{paper.record_id}",
        ]
        return tuple(
            cache_key("paper", self._provider_fingerprint(), value)
            for value in raw_keys if value
        )

    async def _hydrate_cached_metadata(
        self, papers: list[AcademicPaper], *, allowed_sources: set[str] | None = None
    ) -> tuple[list[AcademicPaper], list[str]]:
        if self.shared_store is None or not papers:
            return papers, []
        hydrated: list[AcademicPaper] = []
        warnings: list[str] = []
        allowed = allowed_sources if allowed_sources is not None else {
            p.name for p in self._active_providers()
        }
        try:
            for paper in papers:
                cached_paper: AcademicPaper | None = None
                for key in self._paper_metadata_keys(paper):
                    cached_raw = await self.shared_store.get_metadata(key)
                    if cached_raw:
                        cached_paper = AcademicPaper.model_validate_json(cached_raw)
                        if any(r.source not in allowed for r in cached_paper.source_records):
                            cached_paper = None
                            continue
                        break
                if cached_paper is None:
                    hydrated.append(paper)
                    continue
                merged, merge_warnings = merge_papers(cached_paper, paper)
                hydrated.append(merged)
                warnings.extend(merge_warnings)
            return hydrated, warnings
        except Exception as error:
            logger.warning(
                "academic.cache.redis_metadata_read_failed",
                error_type=type(error).__name__,
            )
            return papers, []

    async def _store_metadata(self, papers: list[AcademicPaper]) -> None:
        if self.shared_store is None or not papers:
            return
        try:
            for paper in papers:
                serialized = paper.model_dump_json()
                for key in self._paper_metadata_keys(paper):
                    await self.shared_store.put_metadata(
                        key, serialized, self.config.metadata_cache_ttl_s
                    )
        except Exception as error:
            logger.warning(
                "academic.cache.redis_metadata_write_failed",
                error_type=type(error).__name__,
            )


def get_academic_search_service() -> AcademicSearchService:
    global _GLOBAL_ACADEMIC_SERVICE
    if _GLOBAL_ACADEMIC_SERVICE is None:
        config = load_agent_runtime_config().tools.academic
        shared_store = create_academic_redis_store(config.reliability)
        _GLOBAL_ACADEMIC_SERVICE = AcademicSearchService(
            config, shared_store=shared_store
        )
    return _GLOBAL_ACADEMIC_SERVICE


async def close_academic_search_service() -> None:
    """Release the optional Redis pool during application shutdown."""
    global _GLOBAL_ACADEMIC_SERVICE
    service = _GLOBAL_ACADEMIC_SERVICE
    _GLOBAL_ACADEMIC_SERVICE = None
    if service is not None and service.shared_store is not None:
        await service.shared_store.close()


async def academic_metrics_snapshot() -> dict[str, object]:
    """Return local live metrics plus durable daily Redis counters when available."""
    snapshot: dict[str, object] = global_academic_metrics.snapshot()
    service = _GLOBAL_ACADEMIC_SERVICE
    if service is not None and service.shared_store is not None:
        try:
            snapshot["redis_daily"] = await service.shared_store.daily_metrics()
            snapshot["redis_available"] = True
        except Exception:
            snapshot["redis_available"] = False
    else:
        snapshot["redis_available"] = False
    return snapshot
