"""Additional regression tests for academic-search edge cases."""

from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from core.tool_config import AcademicToolsConfig
from server.tools.academic.contracts import (
    AcademicAuthor,
    AcademicIdentifiers,
    AcademicPaper,
    AcademicSearchInput,
    AcademicSourceRecord,
)
from server.tools.academic.arxiv import ArxivProvider
from server.tools.academic.normalize import are_papers_duplicate, merge_papers
from server.tools.academic.semantic_scholar import SemanticScholarProvider
from server.tools.academic.service import AcademicSearchService
from server.tools.web.cache import TTLCache


class RecordingProvider:
    name = "arxiv"

    def __init__(self, papers: list[AcademicPaper]) -> None:
        self.papers = papers
        self.requests: list[AcademicSearchInput] = []
        self.calls = 0

    async def search(self, request: AcademicSearchInput) -> list[AcademicPaper]:
        self.calls += 1
        self.requests.append(request)
        return list(self.papers)


class RuntimeFailureProvider(RecordingProvider):
    async def search(self, request: AcademicSearchInput) -> list[AcademicPaper]:
        self.calls += 1
        self.requests.append(request)
        raise RuntimeError("unexpected provider failure")


def make_paper(index: int = 0, *, title: str | None = None) -> AcademicPaper:
    arxiv_id = f"2401.0000{index}"
    return AcademicPaper(
        record_id=f"arxiv:{arxiv_id}",
        title=title or f"Paper {index}",
        authors=[AcademicAuthor(name="Test Author")],
        publication_year=2024,
        publication_status="preprint",
        identifiers=AcademicIdentifiers(arxiv_id=arxiv_id),
        landing_url=f"https://arxiv.org/abs/{arxiv_id}",
        source_records=[
            AcademicSourceRecord(
                source="arxiv",
                source_id=f"{arxiv_id}v1",
                rank=index + 1,
                retrieved_at=datetime.now(timezone.utc),
            )
        ],
    )


@pytest.mark.asyncio
async def test_service_applies_configured_result_cap_to_provider_and_response():
    provider = RecordingProvider([make_paper(index) for index in range(4)])
    service = AcademicSearchService(
        AcademicToolsConfig(max_results=2),
        providers=[provider],
        cache=TTLCache(ttl_s=0),
    )

    response = await service.search(
        AcademicSearchInput(query="paper", max_results=10)
    )

    assert provider.requests[0].max_results == 2
    assert len(response.papers) == 2


@pytest.mark.asyncio
async def test_cache_hit_preserves_current_original_query_when_query_en_matches():
    provider = RecordingProvider([make_paper(title="Attention Is All You Need")])
    service = AcademicSearchService(
        AcademicToolsConfig(),
        providers=[provider],
        cache=TTLCache(ttl_s=3600),
    )

    await service.search(
        AcademicSearchInput(query="第一种中文问法", query_en="Attention Is All You Need")
    )
    cached = await service.search(
        AcademicSearchInput(query="第二种中文问法", query_en="Attention Is All You Need")
    )

    assert provider.calls == 1
    assert cached.query == "第二种中文问法"
    assert cached.query_used == "Attention Is All You Need"


@pytest.mark.asyncio
async def test_service_converts_unexpected_provider_exception_to_source_error():
    provider = RuntimeFailureProvider([])
    service = AcademicSearchService(
        AcademicToolsConfig(),
        providers=[provider],
        cache=TTLCache(ttl_s=0),
    )

    response = await service.search(AcademicSearchInput(query="paper"))

    assert response.papers == []
    assert response.partial is True
    assert response.source_status[0].status == "error"
    assert "unexpected provider failure" in response.source_status[0].message


def test_versioned_arxiv_ids_share_identity_and_are_canonicalized_on_merge():
    first = make_paper(title="Versioned Paper").model_copy(
        update={
            "identifiers": AcademicIdentifiers(arxiv_id="1706.03762v1"),
            "landing_url": "https://arxiv.org/abs/1706.03762",
        }
    )
    second = make_paper(title="Versioned Paper").model_copy(
        update={
            "record_id": "s2:versioned",
            "identifiers": AcademicIdentifiers(arxiv_id="1706.03762"),
            "landing_url": "https://arxiv.org/abs/1706.03762",
        }
    )

    assert are_papers_duplicate(first, second) is True
    merged, _warnings = merge_papers(first, second)
    assert merged.identifiers.arxiv_id == "1706.03762"


def test_merge_keeps_latest_update_and_reports_acl_conflict():
    first = make_paper(title="Published Paper").model_copy(
        update={
            "updated_at": datetime(2023, 1, 1, tzinfo=timezone.utc),
            "publication_year": 2023,
            "publication_status": "peer_reviewed",
            "identifiers": AcademicIdentifiers(acl_id="2023.acl-main.1"),
        }
    )
    second = make_paper(title="Published Paper").model_copy(
        update={
            "record_id": "s2:published",
            "updated_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "publication_year": 2023,
            "publication_status": "peer_reviewed",
            "identifiers": AcademicIdentifiers(acl_id="2023.acl-main.2"),
        }
    )

    merged, warnings = merge_papers(first, second)

    assert merged.updated_at == datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert any("ACL ID conflict" in warning for warning in warnings)


def parse_s2_item(item: dict) -> AcademicPaper:
    payload = {"data": [item]}
    return SemanticScholarProvider()._parse_response_json(
        json.dumps(payload).encode("utf-8")
    )[0]


def test_semantic_scholar_normalizes_naive_publication_date_to_utc():
    paper = parse_s2_item(
        {
            "paperId": "naive-date",
            "title": "A Date Test",
            "publicationDate": "2024-01-02T03:04:05",
            "externalIds": {},
        }
    )

    assert paper.published_at == datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def test_semantic_scholar_discards_untrusted_open_access_pdf_url():
    paper = parse_s2_item(
        {
            "paperId": "unsafe-pdf",
            "title": "A PDF Test",
            "openAccessPdf": {"url": "https://evil.example/paper.pdf"},
            "externalIds": {},
        }
    )

    assert paper.pdf_url is None


def test_semantic_scholar_ignores_non_string_paper_url():
    paper = parse_s2_item(
        {
            "paperId": "bad-url-type",
            "title": "A URL Type Test",
            "url": 123,
            "externalIds": {},
        }
    )

    assert str(paper.landing_url) == (
        "https://www.semanticscholar.org/paper/bad-url-type?utm_source=api"
    )


def test_arxiv_normalizes_naive_atom_dates_to_utc():
    xml = b"""<?xml version='1.0' encoding='UTF-8'?>
    <feed xmlns='http://www.w3.org/2005/Atom'
          xmlns:arxiv='http://arxiv.org/schemas/atom'>
      <entry>
        <id>https://arxiv.org/abs/2401.00001v1</id>
        <title>A Date Test</title>
        <published>2024-01-02T03:04:05</published>
        <updated>2024-01-03T03:04:05</updated>
      </entry>
    </feed>"""

    paper = ArxivProvider()._parse_atom_xml(xml)[0]

    assert paper.first_submitted_at == datetime(
        2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc
    )
    assert paper.updated_at == datetime(2024, 1, 3, 3, 4, 5, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_disabled_search_reports_the_explicit_sources_as_skipped():
    provider = RecordingProvider([])
    service = AcademicSearchService(
        AcademicToolsConfig(enabled=False),
        providers=[provider],
        cache=TTLCache(ttl_s=0),
    )

    response = await service.search(
        AcademicSearchInput(query="paper", sources=["semantic_scholar", "crossref"])
    )

    assert provider.calls == 0
    assert [(status.source, status.status) for status in response.source_status] == [
        ("semantic_scholar", "skipped"),
        ("crossref", "skipped"),
    ]
