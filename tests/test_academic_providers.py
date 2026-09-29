"""Unit tests for arXiv academic provider using MockTransport and local fixtures."""

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
from pathlib import Path
import time
import httpx
import pytest

from core.tool_config import AcademicArxivConfig, AcademicSemanticScholarConfig
from server.tools.academic.arxiv import ArxivProvider
from server.tools.academic.contracts import AcademicSearchInput
from server.tools.academic.provider import (
    AcademicProviderError,
    AcademicRateLimitError,
    AcademicResponseTooLargeError,
    AcademicTimeoutError,
)
from server.tools.academic.semantic_scholar import (
    SemanticScholarProvider,
    _build_s2_paper_url,
    _parse_retry_after,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "academic" / "arxiv_transformer.xml"
S2_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "academic" / "semantic_scholar_lora.json"
)


def _load_transformer_xml() -> bytes:
    return FIXTURE_PATH.read_bytes()


def _load_lora_json() -> bytes:
    return S2_FIXTURE_PATH.read_bytes()


@pytest.mark.asyncio
async def test_arxiv_provider_parses_atom_fixture():
    xml_content = _load_transformer_xml()

    def handler(request: httpx.Request) -> httpx.Response:
        assert "export.arxiv.org" in str(request.url)
        assert "search_query" in request.url.params
        return httpx.Response(200, content=xml_content)

    config = AcademicArxivConfig(min_interval_s=3.0)
    provider = ArxivProvider(config=config, transport=httpx.MockTransport(handler))
    # Reset last request time to avoid waiting
    ArxivProvider._last_request_time = 0.0

    request = AcademicSearchInput(query="Attention Is All You Need", max_results=5)
    papers = await provider.search(request)

    assert len(papers) == 1
    paper = papers[0]
    assert paper.record_id == "arxiv:1706.03762"
    assert paper.title == "Attention Is All You Need"
    assert len(paper.authors) == 8
    author_names = [a.name for a in paper.authors]
    assert "Ashish Vaswani" in author_names
    assert "Illia Polosukhin" in author_names
    assert paper.abstract is not None
    assert "dominant sequence transduction models" in paper.abstract
    assert paper.first_submitted_at is not None
    assert paper.first_submitted_at.year == 2017
    assert paper.updated_at is not None
    assert paper.updated_at.year == 2023
    assert paper.published_at is None
    assert paper.publication_year == 2017
    assert paper.publication_status == "preprint"
    assert (
        paper.venue
        == "Advances in Neural Information Processing Systems 30 (NIPS 2017)"
    )
    assert paper.identifiers.arxiv_id == "1706.03762"
    assert paper.identifiers.doi == "10.48550/arxiv.1706.03762"
    assert str(paper.landing_url) == "https://arxiv.org/abs/1706.03762"
    assert str(paper.pdf_url) == "https://arxiv.org/pdf/1706.03762.pdf"
    assert len(paper.source_records) == 1
    assert paper.source_records[0].source == "arxiv"
    assert paper.source_records[0].source_id == "1706.03762v7"
    assert paper.source_records[0].rank == 1


@pytest.mark.asyncio
async def test_arxiv_provider_marks_withdrawn_submission_explicitly():
    xml_content = _load_transformer_xml().replace(
        b"Attention Is All You Need", b"[Withdrawn] Attention Is All You Need"
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=xml_content)

    provider = ArxivProvider(transport=httpx.MockTransport(handler))
    provider.min_interval_s = 0.01
    ArxivProvider._last_request_time = 0.0

    papers = await provider.search(AcademicSearchInput(query="transformer"))

    assert papers[0].integrity_status == "withdrawn"


@pytest.mark.asyncio
async def test_arxiv_provider_handles_429_rate_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "5"})

    config = AcademicArxivConfig(min_interval_s=3.0)
    provider = ArxivProvider(config=config, transport=httpx.MockTransport(handler))
    ArxivProvider._last_request_time = 0.0

    request = AcademicSearchInput(query="transformer")
    with pytest.raises(AcademicRateLimitError) as exc_info:
        await provider.search(request)
    assert exc_info.value.retry_after_s == 5.0


@pytest.mark.asyncio
async def test_arxiv_provider_handles_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("mock timeout")

    config = AcademicArxivConfig(min_interval_s=3.0, timeout_s=5.0)
    provider = ArxivProvider(config=config, transport=httpx.MockTransport(handler))
    ArxivProvider._last_request_time = 0.0

    request = AcademicSearchInput(query="transformer")
    with pytest.raises(AcademicTimeoutError):
        await provider.search(request)


@pytest.mark.asyncio
async def test_arxiv_provider_handles_response_too_large():
    # 3 MB response exceeds 2 MB limit
    big_content = b"<feed>" + (b" " * (3 * 1024 * 1024)) + b"</feed>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=big_content)

    config = AcademicArxivConfig(min_interval_s=3.0)
    provider = ArxivProvider(config=config, transport=httpx.MockTransport(handler))
    ArxivProvider._last_request_time = 0.0

    request = AcademicSearchInput(query="transformer")
    with pytest.raises(AcademicResponseTooLargeError):
        await provider.search(request)


@pytest.mark.asyncio
async def test_arxiv_provider_handles_malformed_xml():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<broken xml")

    config = AcademicArxivConfig(min_interval_s=3.0)
    provider = ArxivProvider(config=config, transport=httpx.MockTransport(handler))
    ArxivProvider._last_request_time = 0.0

    request = AcademicSearchInput(query="transformer")
    with pytest.raises(AcademicProviderError, match="failed to parse arXiv Atom XML"):
        await provider.search(request)


@pytest.mark.asyncio
async def test_arxiv_provider_rate_limit_interval(monkeypatch):
    sleep_calls = []

    async def mock_sleep(seconds: float):
        sleep_calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_load_transformer_xml())

    config = AcademicArxivConfig(min_interval_s=3.0)
    provider = ArxivProvider(config=config, transport=httpx.MockTransport(handler))

    # Simulate recent request 1.0 second ago
    ArxivProvider._last_request_time = time.monotonic() - 1.0

    request = AcademicSearchInput(query="transformer")
    await provider.search(request)

    assert len(sleep_calls) == 1
    # Should have slept for remaining ~2.0s
    assert 1.5 <= sleep_calls[0] <= 2.1


@pytest.mark.asyncio
async def test_arxiv_provider_concurrency_is_strictly_one():
    current_concurrent = 0
    peak_concurrent = 0

    async def async_handler(request: httpx.Request) -> httpx.Response:
        nonlocal current_concurrent, peak_concurrent
        current_concurrent += 1
        peak_concurrent = max(peak_concurrent, current_concurrent)
        await asyncio.sleep(0.05)
        current_concurrent -= 1
        return httpx.Response(200, content=_load_transformer_xml())

    # Set min_interval_s to small value on provider instance for test speed
    provider = ArxivProvider(transport=httpx.MockTransport(async_handler))
    provider.min_interval_s = 0.01
    ArxivProvider._last_request_time = 0.0

    req1 = AcademicSearchInput(query="transformer attention")
    req2 = AcademicSearchInput(query="transformer architecture")

    res1, res2 = await asyncio.gather(
        provider.search(req1),
        provider.search(req2),
    )

    assert len(res1) == 1
    assert len(res2) == 1
    assert peak_concurrent == 1


@pytest.mark.asyncio
async def test_arxiv_provider_encodes_year_range_in_query():
    captured_params = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_params
        captured_params = dict(request.url.params)
        return httpx.Response(200, content=_load_transformer_xml())

    provider = ArxivProvider(transport=httpx.MockTransport(handler))
    provider.min_interval_s = 0.01
    ArxivProvider._last_request_time = 0.0

    req = AcademicSearchInput(query="transformer", year_from=2017, year_to=2019)
    await provider.search(req)

    search_query = captured_params.get("search_query", "")
    assert "submittedDate:[201701010000 TO 201912312359]" in search_query

    # An open-ended range at the schema's maximum year must cover all of 2100.
    req_open_ended = AcademicSearchInput(query="transformer", year_from=2100)
    await provider.search(req_open_ended)

    search_query = captured_params.get("search_query", "")
    assert "submittedDate:[210001010000 TO 210012312359]" in search_query


@pytest.mark.asyncio
async def test_arxiv_provider_rejects_empty_query():
    provider = ArxivProvider()

    with pytest.raises(AcademicProviderError, match="query cannot be empty"):
        provider._build_search_query("   ")


def test_arxiv_provider_builds_safe_title_phrase_and_topic_fallback():
    provider = ArxivProvider()

    query = provider._build_search_query(
        'LoRA: "Low-Rank" Adaptation-of-Large-Language-Models'
    )

    assert 'ti:"LoRA Low Rank Adaptation of Large Language Models"' in query
    assert "all:LoRA AND all:Low AND all:Rank" in query
    assert '"Low-Rank"' not in query


def test_arxiv_provider_single_term_query_remains_simple():
    provider = ArxivProvider()

    assert provider._build_search_query("transformer") == "all:transformer"


@pytest.mark.asyncio
async def test_semantic_scholar_provider_parses_json_fixture():
    lora_json = _load_lora_json()

    def handler(request: httpx.Request) -> httpx.Response:
        assert "api.semanticscholar.org" in str(request.url)
        assert "/paper/search" in request.url.path
        assert "query" in request.url.params
        assert "fields" in request.url.params
        return httpx.Response(200, content=lora_json)

    config = AcademicSemanticScholarConfig()
    provider = SemanticScholarProvider(
        config=config,
        transport=httpx.MockTransport(handler),
        min_interval_s=0.01,
    )
    SemanticScholarProvider._last_request_time = 0.0

    request = AcademicSearchInput(query="LoRA: Low-Rank Adaptation", max_results=5)
    papers = await provider.search(request)

    assert len(papers) == 1
    paper = papers[0]
    assert paper.record_id == "s2:a804a8eef4cf66ec71f9cf7eb9c2f6d20390f701"
    assert paper.title == "LoRA: Low-Rank Adaptation of Large Language Models"
    assert len(paper.authors) == 8
    author_names = [a.name for a in paper.authors]
    assert "Edward J. Hu" in author_names
    assert "Weizhu Chen" in author_names
    assert paper.abstract is not None
    assert "Low-Rank Adaptation" in paper.abstract
    assert paper.venue == "ICLR"
    # LoRA only has an arXiv DataCite DOI, so it remains a preprint with published_at=None
    assert paper.publication_status == "preprint"
    assert paper.published_at is None
    assert paper.first_submitted_at is not None
    assert paper.first_submitted_at.year == 2021
    assert paper.identifiers.arxiv_id == "2106.09685"
    assert paper.identifiers.doi == "10.48550/arxiv.2106.09685"
    assert (
        paper.identifiers.semantic_scholar_id
        == "a804a8eef4cf66ec71f9cf7eb9c2f6d20390f701"
    )
    assert paper.identifiers.corpus_id == "235458009"
    assert paper.publication_year == 2021
    # Landing URL must route to arXiv abs page, not the preprint DataCite DOI URL
    assert str(paper.landing_url) == "https://arxiv.org/abs/2106.09685"
    assert str(paper.pdf_url) == "https://arxiv.org/pdf/2106.09685.pdf"
    assert len(paper.source_records) == 1
    assert paper.source_records[0].source == "semantic_scholar"
    assert paper.source_records[0].rank == 1
    # utm_source=api attribution parameter present on S2 URL
    assert "utm_source=api" in str(paper.source_records[0].source_url)


@pytest.mark.asyncio
async def test_semantic_scholar_provider_handles_429_rate_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "4.5"})

    provider = SemanticScholarProvider(
        transport=httpx.MockTransport(handler),
        min_interval_s=0.01,
    )
    SemanticScholarProvider._last_request_time = 0.0

    request = AcademicSearchInput(query="LoRA")
    with pytest.raises(AcademicRateLimitError) as exc_info:
        await provider.search(request)
    assert exc_info.value.retry_after_s == 4.5
    assert SemanticScholarProvider._next_allowed_time > time.monotonic() + 4.0
    SemanticScholarProvider._next_allowed_time = 0.0


@pytest.mark.asyncio
async def test_semantic_scholar_provider_handles_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("mock timeout")

    provider = SemanticScholarProvider(
        transport=httpx.MockTransport(handler),
        min_interval_s=0.01,
    )
    SemanticScholarProvider._last_request_time = 0.0

    request = AcademicSearchInput(query="LoRA")
    with pytest.raises(AcademicTimeoutError):
        await provider.search(request)


@pytest.mark.asyncio
async def test_semantic_scholar_provider_handles_response_too_large():
    big_json = b'{"data": [' + (b" " * (3 * 1024 * 1024)) + b"]}"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=big_json)

    provider = SemanticScholarProvider(
        transport=httpx.MockTransport(handler),
        min_interval_s=0.01,
    )
    SemanticScholarProvider._last_request_time = 0.0

    request = AcademicSearchInput(query="LoRA")
    with pytest.raises(AcademicResponseTooLargeError):
        await provider.search(request)


@pytest.mark.asyncio
async def test_semantic_scholar_provider_handles_malformed_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{ broken json")

    provider = SemanticScholarProvider(
        transport=httpx.MockTransport(handler),
        min_interval_s=0.01,
    )
    SemanticScholarProvider._last_request_time = 0.0

    request = AcademicSearchInput(query="LoRA")
    with pytest.raises(
        AcademicProviderError, match="failed to parse Semantic Scholar JSON response"
    ):
        await provider.search(request)


@pytest.mark.asyncio
async def test_semantic_scholar_provider_api_key_header_and_no_leak(monkeypatch):
    captured_headers = {}
    captured_params = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_headers, captured_params
        captured_headers = dict(request.headers)
        captured_params = dict(request.url.params)
        return httpx.Response(200, content=_load_lora_json())

    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "secret-test-key-12345")

    provider = SemanticScholarProvider(
        transport=httpx.MockTransport(handler),
        min_interval_s=0.01,
    )
    SemanticScholarProvider._last_request_time = 0.0

    request = AcademicSearchInput(query="LoRA")
    papers = await provider.search(request)
    assert len(papers) == 1

    # Header x-api-key present
    assert captured_headers.get("x-api-key") == "secret-test-key-12345"
    # Never in query params
    assert "secret-test-key-12345" not in str(captured_params)
    assert "api_key" not in captured_params
    assert "x-api-key" not in captured_params


@pytest.mark.asyncio
async def test_semantic_scholar_acl_url_priority():
    raw_payload = {
        "data": [
            {
                "paperId": "acl-paper-1",
                "title": "BERT: Pre-training of Deep Bidirectional Transformers",
                "authors": [{"name": "Jacob Devlin"}],
                "abstract": "We introduce BERT...",
                "year": 2019,
                "publicationDate": "2019-06-02",
                "venue": "NAACL",
                "externalIds": {
                    "ACL": "N19-1423",
                    "DOI": "10.18653/v1/N19-1423",
                    "ArXiv": "1810.04805",
                },
                "url": "https://www.semanticscholar.org/paper/acl-paper-1",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(raw_payload).encode("utf-8"))

    provider = SemanticScholarProvider(
        transport=httpx.MockTransport(handler),
        min_interval_s=0.01,
    )
    SemanticScholarProvider._last_request_time = 0.0

    request = AcademicSearchInput(query="BERT")
    papers = await provider.search(request)

    assert len(papers) == 1
    paper = papers[0]
    # ACL Anthology landing URL has highest priority
    assert str(paper.landing_url) == "https://aclanthology.org/N19-1423/"
    assert paper.identifiers.acl_id == "N19-1423"
    assert paper.identifiers.doi == "10.18653/v1/n19-1423"
    assert paper.identifiers.arxiv_id == "1810.04805"


@pytest.mark.asyncio
async def test_semantic_scholar_query_preprocessing_hyphens():
    captured_query = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_query
        captured_query = request.url.params.get("query")
        return httpx.Response(200, content=b'{"data": []}')

    provider = SemanticScholarProvider(
        transport=httpx.MockTransport(handler),
        min_interval_s=0.01,
    )
    SemanticScholarProvider._last_request_time = 0.0

    # Query with hyphens and syntax characters that would break Lucene relevance search
    req = AcademicSearchInput(query='LoRA: "Low-Rank" Adaptation-of-LLMs')
    await provider.search(req)

    assert captured_query is not None
    # Hyphens must be replaced by spaces so Lucene doesn't negate words
    assert "-" not in captured_query
    assert '"' not in captured_query
    assert "Low Rank" in captured_query
    assert "Adaptation of LLMs" in captured_query

    unicode_query = provider._build_params(
        AcademicSearchInput(query="Low\N{NON-BREAKING HYPHEN}Rank Adaptation")
    )["query"]
    assert unicode_query == "Low Rank Adaptation"


@pytest.mark.asyncio
async def test_semantic_scholar_provider_rejects_missing_data_or_error_payload():
    # 1. API error payload: {"error": "Invalid API key"}
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"error": "Invalid API key"}')

    p1 = SemanticScholarProvider(
        transport=httpx.MockTransport(error_handler), min_interval_s=0.01
    )
    SemanticScholarProvider._last_request_time = 0.0
    with pytest.raises(
        AcademicProviderError, match="Semantic Scholar API error: Invalid API key"
    ):
        await p1.search(AcademicSearchInput(query="LoRA"))

    # 2. Missing data field: {"total": 0, "offset": 0}
    def missing_data_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"total": 0, "offset": 0}')

    p2 = SemanticScholarProvider(
        transport=httpx.MockTransport(missing_data_handler), min_interval_s=0.01
    )
    SemanticScholarProvider._last_request_time = 0.0
    with pytest.raises(AcademicProviderError, match="missing required 'data' field"):
        await p2.search(AcademicSearchInput(query="LoRA"))

    # 3. Non-list data field: {"data": "bad"}
    def non_list_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"data": "bad"}')

    p3 = SemanticScholarProvider(
        transport=httpx.MockTransport(non_list_handler), min_interval_s=0.01
    )
    SemanticScholarProvider._last_request_time = 0.0
    with pytest.raises(AcademicProviderError, match="must be a list"):
        await p3.search(AcademicSearchInput(query="LoRA"))

    # 4. Valid empty list data: {"data": []} -> successfully returns empty list
    def empty_list_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"data": []}')

    p4 = SemanticScholarProvider(
        transport=httpx.MockTransport(empty_list_handler), min_interval_s=0.01
    )
    SemanticScholarProvider._last_request_time = 0.0
    papers = await p4.search(AcademicSearchInput(query="LoRA"))
    assert papers == []


def test_semantic_scholar_url_is_fixed_to_trusted_https_origin():
    assert _build_s2_paper_url("paper-id", "http://evil.example/paper") == (
        "https://www.semanticscholar.org/paper/paper-id?utm_source=api"
    )
    assert _build_s2_paper_url(
        "paper-id",
        "https://www.semanticscholar.org/paper/paper-id?notutm_source=api.evil",
    ) == (
        "https://www.semanticscholar.org/paper/paper-id?"
        "notutm_source=api.evil&utm_source=api"
    )


def test_retry_after_supports_http_date():
    retry_at = datetime.now(timezone.utc) + timedelta(seconds=10)
    delay = _parse_retry_after(format_datetime(retry_at), default_s=1.0)
    assert 8.0 <= delay <= 10.0


def test_semantic_scholar_venue_without_verified_identifier_is_not_peer_reviewed():
    payload = {
        "data": [
            {
                "paperId": "venue-only",
                "title": "An Unverified Venue Record",
                "year": 2024,
                "publicationDate": None,
                "venue": "arXiv",
                "externalIds": {"CorpusId": 123},
            }
        ]
    }

    paper = SemanticScholarProvider()._parse_response_json(
        json.dumps(payload).encode("utf-8")
    )[0]

    assert paper.publication_status == "preprint"
    assert paper.publication_year == 2024
    assert paper.identifiers.corpus_id == "123"
    assert str(paper.landing_url) == (
        "https://www.semanticscholar.org/paper/venue-only?utm_source=api"
    )
