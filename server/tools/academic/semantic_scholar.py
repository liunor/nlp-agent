"""Semantic Scholar Graph API client implementing AcademicProvider protocol."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import os
import re
import time
from typing import Any
import unicodedata
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx

from core.outbound_network import OutboundNetworkPolicy
from core.tool_config import AcademicSemanticScholarConfig
from server.tools.academic.contracts import (
    AcademicAuthor,
    AcademicIdentifiers,
    AcademicPaper,
    AcademicSearchInput,
    AcademicSourceRecord,
)
from server.tools.academic.normalize import (
    canonical_arxiv_pdf_url,
    is_arxiv_doi,
    normalize_doi,
    parse_arxiv_id,
    select_canonical_landing_url,
)
from server.tools.academic.provider import (
    AcademicProvider,
    AcademicProviderError,
    AcademicRateLimitError,
    AcademicResponseTooLargeError,
    AcademicTimeoutError,
)

DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MB safety limit
SEARCH_FIELDS = "paperId,title,abstract,authors,year,publicationDate,venue,externalIds,url,openAccessPdf"
TRUSTED_ACADEMIC_URL_HOSTS = frozenset(
    {"arxiv.org", "doi.org", "aclanthology.org", "www.semanticscholar.org"}
)


def _build_s2_paper_url(paper_id: str, raw_url: str | None = None) -> str:
    """Build a validated Semantic Scholar HTTPS URL with API attribution."""
    canonical = f"https://www.semanticscholar.org/paper/{quote(paper_id, safe='')}"
    base = canonical
    if isinstance(raw_url, str) and raw_url.strip():
        try:
            parsed = urlsplit(raw_url.strip())
            if (
                parsed.scheme == "https"
                and parsed.hostname == "www.semanticscholar.org"
                and parsed.username is None
                and parsed.password is None
                and parsed.port is None
            ):
                base = urlunsplit(parsed)
        except ValueError:
            base = canonical

    parsed = urlsplit(base)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["utm_source"] = "api"
    return urlunsplit(parsed._replace(query=urlencode(query)))


def _parse_retry_after(value: str | None, *, default_s: float) -> float:
    """Parse Retry-After seconds or an HTTP-date into a non-negative delay."""
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return default_s


def _normalize_datetime(value: datetime) -> datetime:
    """Make provider dates comparable with the service's UTC timestamps."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _trusted_academic_url(raw_url: str | None) -> str | None:
    if not isinstance(raw_url, str) or not raw_url.strip():
        return None
    try:
        parsed = urlsplit(raw_url.strip())
        if (
            parsed.scheme != "https"
            or parsed.hostname not in TRUSTED_ACADEMIC_URL_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
        ):
            return None
    except ValueError:
        return None
    return urlunsplit(parsed)


class SemanticScholarProvider(AcademicProvider):
    """Client for Semantic Scholar Graph API with rate limiting and safety controls."""

    name: str = "semantic_scholar"
    _global_lock: asyncio.Lock = asyncio.Lock()
    _last_request_time: float = 0.0
    _next_allowed_time: float = 0.0

    def __init__(
        self,
        config: AcademicSemanticScholarConfig | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        min_interval_s: float = 1.0,
        network_policy: OutboundNetworkPolicy | None = None,
    ) -> None:
        self.config = config or AcademicSemanticScholarConfig()
        self.base_url = self.config.base_url.rstrip("/")
        self.timeout_s = float(self.config.timeout_s)
        self.api_key_env = self.config.api_key_env
        self.max_response_bytes = max_response_bytes
        self.min_interval_s = min_interval_s
        self._transport = transport
        self.network_policy = network_policy or OutboundNetworkPolicy.from_environment()

    def _get_api_key(self) -> str | None:
        if not self.api_key_env:
            return None
        key = os.environ.get(self.api_key_env, "").strip()
        return key or None

    def _build_params(self, request: AcademicSearchInput) -> dict[str, Any]:
        raw_query = (request.query_en or request.query).strip()
        if not raw_query:
            raise AcademicProviderError("query cannot be empty")

        # The relevance endpoint does not support special query syntax and documents
        # that hyphenated terms do not match. Normalize all Unicode dash punctuation.
        clean_query = "".join(
            " " if char == "_" or unicodedata.category(char) == "Pd" else char
            for char in raw_query
        )
        clean_query = re.sub(r'[":;`~^/\\(){}\[\]]', " ", clean_query)
        clean_query = re.sub(r"\s+", " ", clean_query).strip()
        query_term = clean_query if clean_query else raw_query

        max_results = min(max(1, request.max_results), 10)
        params: dict[str, Any] = {
            "query": query_term,
            "fields": SEARCH_FIELDS,
            "limit": max_results,
        }

        if request.year_from or request.year_to:
            if request.year_from and request.year_to:
                params["year"] = f"{request.year_from}-{request.year_to}"
            elif request.year_from:
                params["year"] = f"{request.year_from}-"
            elif request.year_to:
                params["year"] = f"-{request.year_to}"

        return params

    async def search(self, request: AcademicSearchInput) -> list[AcademicPaper]:
        if not self.config.enabled:
            return []

        params = self._build_params(request)
        url = f"{self.base_url}/paper/search"

        headers: dict[str, str] = {
            "User-Agent": "nlp-agent/1.0 (+academic-search)",
            "Accept": "application/json",
        }
        api_key = self._get_api_key()
        if api_key:
            headers["x-api-key"] = api_key

        async with self._global_lock:
            now = time.monotonic()
            next_start = max(
                self.__class__._last_request_time + self.min_interval_s,
                self.__class__._next_allowed_time,
            )
            if now < next_start:
                await asyncio.sleep(next_start - now)

            limits = httpx.Limits(max_connections=1, max_keepalive_connections=1)
            client_kwargs: dict[str, Any] = {
                "timeout": httpx.Timeout(self.timeout_s),
                "limits": limits,
                "headers": headers,
                "trust_env": False,
            }
            if self._transport is not None:
                client_kwargs["transport"] = self._transport
            else:
                client_kwargs["proxy"] = self.network_policy.proxy_for_url(url)

            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    async with client.stream("GET", url, params=params) as response:
                        if response.status_code == 429:
                            retry_after = response.headers.get("Retry-After")
                            retry_s = _parse_retry_after(
                                retry_after,
                                default_s=self.min_interval_s,
                            )
                            self.__class__._next_allowed_time = max(
                                self.__class__._next_allowed_time,
                                time.monotonic() + retry_s,
                            )
                            raise AcademicRateLimitError(
                                "Semantic Scholar rate limit exceeded (429)",
                                retry_after_s=retry_s,
                            )
                        if response.status_code != 200:
                            raise AcademicProviderError(
                                f"Semantic Scholar API HTTP {response.status_code}: {response.reason_phrase}"
                            )

                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > self.max_response_bytes:
                                raise AcademicResponseTooLargeError(
                                    f"Semantic Scholar response exceeded {self.max_response_bytes} bytes"
                                )
            except httpx.TimeoutException as err:
                raise AcademicTimeoutError(
                    f"Semantic Scholar request timed out after {self.timeout_s}s"
                ) from err
            except httpx.RequestError as err:
                err_msg = str(err).strip() or repr(err)
                raise AcademicProviderError(
                    f"Semantic Scholar network request error ({type(err).__name__}): {err_msg}"
                ) from err
            finally:
                self.__class__._last_request_time = time.monotonic()

        return self._parse_response_json(bytes(body))

    def _parse_response_json(self, raw_bytes: bytes) -> list[AcademicPaper]:
        try:
            payload = json.loads(raw_bytes.decode("utf-8"))
        except Exception as err:
            raise AcademicProviderError(
                f"failed to parse Semantic Scholar JSON response: {err}"
            ) from err

        if not isinstance(payload, dict):
            raise AcademicProviderError(
                f"Semantic Scholar returned unexpected non-dictionary JSON: {payload!r}"
            )

        # Detect error responses from API
        if "error" in payload:
            raise AcademicProviderError(
                f"Semantic Scholar API error: {payload.get('error')}"
            )

        if "message" in payload and "data" not in payload:
            raise AcademicProviderError(
                f"Semantic Scholar API message: {payload.get('message')}"
            )

        if "data" not in payload:
            raise AcademicProviderError(
                f"Semantic Scholar response missing required 'data' field: {payload!r}"
            )

        data_items = payload["data"]
        if not isinstance(data_items, list):
            raise AcademicProviderError(
                f"Semantic Scholar 'data' field must be a list, got {type(data_items).__name__}"
            )

        papers: list[AcademicPaper] = []
        now = datetime.now(timezone.utc)

        for rank, item in enumerate(data_items, start=1):
            if not isinstance(item, dict):
                continue

            paper_id = item.get("paperId")
            if not paper_id or not isinstance(paper_id, str):
                continue
            paper_id = paper_id.strip()

            raw_title = item.get("title")
            title = (
                re.sub(r"\s+", " ", raw_title).strip()
                if isinstance(raw_title, str)
                else ""
            )
            if not title:
                continue

            raw_abstract = item.get("abstract")
            abstract = (
                re.sub(r"\s+", " ", raw_abstract).strip()
                if isinstance(raw_abstract, str) and raw_abstract.strip()
                else None
            )

            # Authors
            authors: list[AcademicAuthor] = []
            for raw_author in item.get("authors") or []:
                if not isinstance(raw_author, dict):
                    continue
                name = raw_author.get("name")
                if isinstance(name, str) and name.strip():
                    author_id = raw_author.get("authorId")
                    authors.append(
                        AcademicAuthor(
                            name=name.strip(),
                            author_id=str(author_id).strip() if author_id else None,
                        )
                    )

            # External IDs (case-insensitive mapping)
            ext_ids = item.get("externalIds") or {}
            ext_lower: dict[str, Any] = {}
            if isinstance(ext_ids, dict):
                for k, v in ext_ids.items():
                    if isinstance(k, str) and v:
                        ext_lower[k.lower()] = str(v).strip()

            raw_doi = ext_lower.get("doi")
            doi = normalize_doi(raw_doi)

            raw_arxiv = ext_lower.get("arxiv")
            arxiv_id: str | None = None
            if raw_arxiv:
                try:
                    arxiv_id, _ = parse_arxiv_id(raw_arxiv)
                except ValueError:
                    arxiv_id = None

            acl_id = ext_lower.get("acl")
            if acl_id:
                acl_id = acl_id.strip().strip("/") or None

            corpus_id = ext_lower.get("corpusid")

            raw_year = item.get("year")
            publication_year: int | None = None
            if isinstance(raw_year, int) and not isinstance(raw_year, bool):
                if 1000 <= raw_year <= 2100:
                    publication_year = raw_year
            elif isinstance(raw_year, str) and raw_year.strip().isdigit():
                parsed_year = int(raw_year.strip())
                if 1000 <= parsed_year <= 2100:
                    publication_year = parsed_year

            # Publication Date
            pub_date_str = item.get("publicationDate")
            parsed_date: datetime | None = None

            if isinstance(pub_date_str, str) and pub_date_str.strip():
                try:
                    cleaned_date = pub_date_str.strip()
                    if len(cleaned_date) == 10:  # YYYY-MM-DD
                        parsed_date = _normalize_datetime(
                            datetime.fromisoformat(f"{cleaned_date}T00:00:00+00:00")
                        )
                    else:
                        parsed_date = _normalize_datetime(
                            datetime.fromisoformat(cleaned_date.replace("Z", "+00:00"))
                        )
                except ValueError:
                    pass

            venue = item.get("venue")
            venue_clean = (
                venue.strip() if isinstance(venue, str) and venue.strip() else None
            )

            # A DOI or venue label alone does not prove peer review. ACL IDs are
            # accepted-publication identifiers; other formal metadata is completed
            # by a later Crossref/ACL verification stage.
            venue_is_preprint = bool(
                venue_clean
                and venue_clean.casefold()
                in {"arxiv", "corr", "biorxiv", "medrxiv", "ssrn"}
            )
            is_preprint = bool(
                arxiv_id or (doi and is_arxiv_doi(doi)) or venue_is_preprint
            ) and not (doi and not is_arxiv_doi(doi))

            if acl_id:
                publication_status = "peer_reviewed"
                published_at = parsed_date
                first_submitted_at = None
            elif is_preprint:
                publication_status = "preprint"
                published_at = None
                first_submitted_at = parsed_date
            else:
                publication_status = "unknown"
                published_at = parsed_date
                first_submitted_at = None

            if publication_year is None and parsed_date is not None:
                publication_year = parsed_date.year

            s2_paper_url = _build_s2_paper_url(paper_id, item.get("url"))

            # Canonical landing URL selection
            identifiers = AcademicIdentifiers(
                doi=doi,
                arxiv_id=arxiv_id,
                acl_id=acl_id,
                semantic_scholar_id=paper_id,
                corpus_id=corpus_id,
            )
            landing_url = select_canonical_landing_url(identifiers, s2_paper_url)

            # PDF URL
            pdf_url: str | None = None
            open_access_pdf = item.get("openAccessPdf")
            if isinstance(open_access_pdf, dict):
                oa_url = open_access_pdf.get("url")
                pdf_url = _trusted_academic_url(oa_url)
            if not pdf_url and arxiv_id:
                pdf_url = canonical_arxiv_pdf_url(arxiv_id)
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
                record_id=f"s2:{paper_id}",
                title=title,
                authors=authors,
                abstract=abstract,
                first_submitted_at=first_submitted_at,
                updated_at=None,
                published_at=published_at,
                publication_year=publication_year,
                publication_status=publication_status,
                venue=venue_clean,
                identifiers=identifiers,
                landing_url=landing_url,
                pdf_url=pdf_url,
                integrity_status=integrity_status,
                source_records=[
                    AcademicSourceRecord(
                        source="semantic_scholar",
                        source_id=paper_id,
                        source_url=s2_paper_url,
                        rank=rank,
                        retrieved_at=now,
                    )
                ],
            )
            papers.append(paper)

        return papers
