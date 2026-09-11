"""arXiv Atom API client implementing AcademicProvider protocol."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import re
import time
from typing import Any

import defusedxml.ElementTree as ET
import httpx

from core.outbound_network import OutboundNetworkPolicy
from core.tool_config import AcademicArxivConfig
from server.tools.academic.contracts import (
    AcademicAuthor,
    AcademicIdentifiers,
    AcademicPaper,
    AcademicSearchInput,
    AcademicSourceRecord,
)
from server.tools.academic.normalize import (
    canonical_arxiv_landing_url,
    canonical_arxiv_pdf_url,
    normalize_doi,
    parse_arxiv_id,
)
from server.tools.academic.provider import (
    AcademicProvider,
    AcademicProviderError,
    AcademicRateLimitError,
    AcademicResponseTooLargeError,
    AcademicTimeoutError,
)
from server.tools.academic.runtime import AcademicSharedStore

ATOM_NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}
DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MB safety limit


class ArxivProvider(AcademicProvider):
    """Client for arXiv export Atom API with strict rate limiting and safety controls."""

    name: str = "arxiv"
    _global_lock: asyncio.Lock = asyncio.Lock()
    _last_request_time: float = 0.0

    def __init__(
        self,
        config: AcademicArxivConfig | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        shared_store: AcademicSharedStore | None = None,
        network_policy: OutboundNetworkPolicy | None = None,
    ) -> None:
        self.config = config or AcademicArxivConfig()
        self.base_url = self.config.base_url
        self.min_interval_s = float(self.config.min_interval_s)
        self.timeout_s = float(self.config.timeout_s)
        self.max_response_bytes = max_response_bytes
        self._transport = transport
        self._shared_store = shared_store
        self.network_policy = network_policy or OutboundNetworkPolicy.from_environment()

    def _build_search_query(self, query: str) -> str:
        clean = query.strip()
        if not clean:
            raise AcademicProviderError("query cannot be empty")

        # arXiv treats punctuation such as a hyphen as query syntax.  Passing a
        # paper title through unchanged can therefore turn ``Low-Rank`` into a
        # negation-like expression and produce no results.  Build the query
        # from plain searchable terms and add an exact title phrase branch.
        phrase = re.sub(r"[-‐‑‒–—―_/:]+", " ", clean)
        phrase = re.sub(r"[^\w.\s]", " ", phrase, flags=re.UNICODE)
        phrase = re.sub(r"\s+", " ", phrase).strip()
        words = [word for word in phrase.split() if word]
        words = [w for w in words if w]
        if not words:
            raise AcademicProviderError("query cannot be empty")
        if len(words) == 1:
            return f"all:{words[0]}"

        title_phrase = f'ti:"{phrase}"'
        all_terms = " AND ".join(f"all:{word}" for word in words)
        return f"({title_phrase} OR ({all_terms}))"

    async def search(self, request: AcademicSearchInput) -> list[AcademicPaper]:
        if not self.config.enabled:
            return []

        query_term = (request.query_en or request.query).strip()
        search_query = self._build_search_query(query_term)
        if request.year_from or request.year_to:
            start_dt = (
                f"{request.year_from:04d}01010000"
                if request.year_from
                else "190001010000"
            )
            end_dt = (
                f"{request.year_to:04d}12312359" if request.year_to else "210012312359"
            )
            search_query = (
                f"({search_query}) AND submittedDate:[{start_dt} TO {end_dt}]"
            )

        max_results = min(max(1, request.max_results), 10)
        sort_by = "submittedDate" if request.sort == "recent" else "relevance"

        params = {
            "search_query": search_query,
            "start": 0,
            "max_results": max_results,
            "sortBy": sort_by,
            "sortOrder": "descending",
        }

        async with self._global_lock:
            if self._shared_store is not None:
                try:
                    await self._shared_store.acquire_rate_limit(
                        self.name, self.min_interval_s
                    )
                except Exception as exc:
                    # Redis is authoritative once configured. Falling back to a
                    # process-local slot could violate arXiv's global interval when
                    # multiple application instances are running.
                    raise AcademicProviderError(
                        "arXiv paused: distributed rate limiter is unavailable"
                    ) from exc
            else:
                now = time.monotonic()
                elapsed = now - self.__class__._last_request_time
                if elapsed < self.min_interval_s:
                    await asyncio.sleep(self.min_interval_s - elapsed)

            limits = httpx.Limits(max_connections=1, max_keepalive_connections=1)
            client_kwargs: dict[str, Any] = {
                "timeout": httpx.Timeout(self.timeout_s),
                "limits": limits,
                "headers": {"User-Agent": "nlp-agent/1.0 (+academic-search)"},
                "trust_env": False,
            }
            if self._transport is not None:
                client_kwargs["transport"] = self._transport
            else:
                client_kwargs["proxy"] = self.network_policy.proxy_for_url(
                    self.base_url
                )

            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    async with client.stream(
                        "GET", self.base_url, params=params
                    ) as response:
                        if response.status_code == 429:
                            retry_after = response.headers.get("Retry-After")
                            retry_s = (
                                float(retry_after)
                                if retry_after and retry_after.isdigit()
                                else 3.0
                            )
                            raise AcademicRateLimitError(
                                "arXiv rate limit exceeded (429)",
                                retry_after_s=retry_s,
                            )
                        if response.status_code != 200:
                            raise AcademicProviderError(
                                f"arXiv API HTTP {response.status_code}: {response.reason_phrase}"
                            )

                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > self.max_response_bytes:
                                raise AcademicResponseTooLargeError(
                                    f"arXiv response exceeded {self.max_response_bytes} bytes"
                                )
            except httpx.TimeoutException as err:
                raise AcademicTimeoutError(
                    f"arXiv request timed out after {self.timeout_s}s"
                ) from err
            except httpx.RequestError as err:
                err_msg = str(err).strip() or repr(err)
                raise AcademicProviderError(
                    f"arXiv network request error ({type(err).__name__}): {err_msg}"
                ) from err
            finally:
                self.__class__._last_request_time = time.monotonic()

        return self._parse_atom_xml(bytes(body))

    def _parse_atom_xml(self, xml_bytes: bytes) -> list[AcademicPaper]:
        try:
            root = ET.fromstring(xml_bytes)
        except Exception as err:
            raise AcademicProviderError(
                f"failed to parse arXiv Atom XML: {err}"
            ) from err

        papers: list[AcademicPaper] = []
        now = datetime.now(timezone.utc)

        for rank, entry in enumerate(
            root.findall("atom:entry", ATOM_NAMESPACES),
            start=1,
        ):
            id_elem = entry.find("atom:id", ATOM_NAMESPACES)
            if id_elem is None or not id_elem.text:
                continue
            raw_id = id_elem.text.strip()
            try:
                canonical_id, versioned_id = parse_arxiv_id(raw_id)
            except ValueError:
                continue

            title_elem = entry.find("atom:title", ATOM_NAMESPACES)
            raw_title = (
                title_elem.text if title_elem is not None and title_elem.text else ""
            )
            title = re.sub(r"\s+", " ", raw_title).strip()
            if not title:
                continue

            summary_elem = entry.find("atom:summary", ATOM_NAMESPACES)
            raw_summary = (
                summary_elem.text
                if summary_elem is not None and summary_elem.text
                else None
            )
            abstract = re.sub(r"\s+", " ", raw_summary).strip() if raw_summary else None

            authors: list[AcademicAuthor] = []
            for author_elem in entry.findall("atom:author", ATOM_NAMESPACES):
                name_elem = author_elem.find("atom:name", ATOM_NAMESPACES)
                if name_elem is not None and name_elem.text:
                    author_name = name_elem.text.strip()
                    if author_name:
                        authors.append(AcademicAuthor(name=author_name))

            first_submitted_at: datetime | None = None
            published_elem = entry.find("atom:published", ATOM_NAMESPACES)
            if published_elem is not None and published_elem.text:
                try:
                    first_submitted_at = datetime.fromisoformat(
                        published_elem.text.strip().replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            updated_at: datetime | None = None
            updated_elem = entry.find("atom:updated", ATOM_NAMESPACES)
            if updated_elem is not None and updated_elem.text:
                try:
                    updated_at = datetime.fromisoformat(
                        updated_elem.text.strip().replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            doi_elem = entry.find("arxiv:doi", ATOM_NAMESPACES)
            doi = normalize_doi(
                doi_elem.text if doi_elem is not None and doi_elem.text else None
            )

            journal_elem = entry.find("arxiv:journal_ref", ATOM_NAMESPACES)
            venue = (
                journal_elem.text.strip()
                if journal_elem is not None and journal_elem.text
                else None
            )
            integrity_status = (
                "withdrawn"
                if re.search(
                    r"\bwithdrawn\b",
                    f"{title}\n{abstract or ''}",
                    flags=re.IGNORECASE,
                )
                else "active"
            )

            paper = AcademicPaper(
                record_id=f"arxiv:{canonical_id}",
                title=title,
                authors=authors,
                abstract=abstract,
                first_submitted_at=first_submitted_at,
                updated_at=updated_at,
                published_at=None,
                publication_year=(
                    first_submitted_at.year if first_submitted_at is not None else None
                ),
                publication_status="preprint",
                venue=venue,
                identifiers=AcademicIdentifiers(
                    arxiv_id=canonical_id,
                    doi=doi,
                ),
                landing_url=canonical_arxiv_landing_url(canonical_id),
                pdf_url=canonical_arxiv_pdf_url(canonical_id),
                integrity_status=integrity_status,
                source_records=[
                    AcademicSourceRecord(
                        source="arxiv",
                        source_id=versioned_id,
                        source_url=f"https://arxiv.org/abs/{versioned_id}",
                        rank=rank,
                        retrieved_at=now,
                    )
                ],
            )
            papers.append(paper)

        return papers
