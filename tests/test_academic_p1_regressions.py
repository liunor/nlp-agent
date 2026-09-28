"""Regression coverage for publication dates and configuration-scoped caches."""

import json
from datetime import datetime, timezone

import pytest

from core.tool_config import AcademicToolsConfig
from server.tools.academic.contracts import AcademicSearchInput
from server.tools.academic.normalize import merge_papers
from server.tools.academic.runtime import RedisAcademicStore
from server.tools.academic.semantic_scholar import SemanticScholarProvider
from server.tools.academic.service import AcademicSearchService
from server.tools.web.cache import TTLCache
from tests.test_academic_reliability import FakeRedis
from tests.test_academic_service import MockProvider, _make_sample_paper


def formal_paper():
    # Synthetic metadata: no claim that these values identify a real publication.
    payload = {"data": [{
        "paperId": "synthetic-paper", "title": "Synthetic publication",
        "externalIds": {"ArXiv": "1706.03762", "DOI": "10.1038/synthetic"},
        "publicationDate": "2020-01-02", "venue": "arXiv",
    }]}
    return SemanticScholarProvider()._parse_response_json(json.dumps(payload).encode())[0]


@pytest.mark.parametrize("reverse", [False, True])
def test_formal_date_survives_arxiv_merge_without_claiming_review(reverse):
    formal = formal_paper()
    date = datetime(2020, 1, 2, tzinfo=timezone.utc)
    assert formal.publication_status == "unknown"
    assert formal.published_at == date
    assert formal.first_submitted_at is None
    assert str(formal.landing_url) == "https://doi.org/10.1038/synthetic"
    preprint = _make_sample_paper()
    merged, _ = merge_papers(*( [preprint, formal] if reverse else [formal, preprint]))
    assert merged.publication_status == "unknown"
    assert merged.published_at == date
    assert merged.first_submitted_at == preprint.first_submitted_at


@pytest.mark.asyncio
@pytest.mark.parametrize("redis", [False, True])
async def test_provider_change_invalidates_query_and_metadata_cache(redis):
    cache = TTLCache(3600)
    store = RedisAcademicStore(FakeRedis()) if redis else None
    old = AcademicSearchService(providers=[MockProvider()], cache=cache, shared_store=store)
    old.providers[0].papers = [_make_sample_paper()]
    request = AcademicSearchInput(query="synthetic publication")
    await old.search(request)
    provider = MockProvider("semantic_scholar", [formal_paper()])
    new = AcademicSearchService(providers=[provider], cache=cache, shared_store=store)
    result = await new.search(request)
    assert provider.call_count == 1
    assert [s.source for s in result.source_status] == ["semantic_scholar"]
    assert result.papers[0].first_submitted_at is None
    assert all(r.source == "semantic_scholar" for r in result.papers[0].source_records)


@pytest.mark.asyncio
async def test_disabled_provider_rejects_injected_old_cache():
    provider = MockProvider("arxiv", [_make_sample_paper()])
    cache = TTLCache(3600)
    service = AcademicSearchService(providers=[provider], cache=cache)
    request = AcademicSearchInput(query="transformer", sources=["arxiv"])
    previous = await service.search(request)
    service.config.arxiv.enabled = False
    cache.put(service._get_cache_key(request, request.query), previous.model_dump_json())
    result = await service.search(request)
    assert provider.call_count == 1
    assert result.papers == []
    assert result.source_status[0].status == "skipped"


@pytest.mark.asyncio
async def test_explicit_single_source_does_not_hydrate_other_source_metadata():
    store = RedisAcademicStore(FakeRedis())
    arxiv = MockProvider("arxiv", [_make_sample_paper()])
    s2 = MockProvider("semantic_scholar", [formal_paper()])
    service = AcademicSearchService(providers=[arxiv, s2], shared_store=store)
    await service.search(AcademicSearchInput(query="synthetic publication"))
    result = await service.search(AcademicSearchInput(
        query="synthetic publication", sources=["semantic_scholar"]
    ))
    assert result.papers[0].first_submitted_at is None
    assert all(r.source == "semantic_scholar" for r in result.papers[0].source_records)


def test_provider_configuration_changes_both_cache_namespaces():
    service = AcademicSearchService(AcademicToolsConfig(), providers=[MockProvider()])
    request = AcademicSearchInput(query="transformer")
    before = service._get_cache_key(request, request.query)
    metadata = service._paper_metadata_keys(_make_sample_paper())
    service.config.arxiv.timeout_s = 20
    assert service._get_cache_key(request, request.query) != before
    assert service._paper_metadata_keys(_make_sample_paper()) != metadata
