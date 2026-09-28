"""Unit tests for academic search data contracts and normalization."""

from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from server.tools.academic.contracts import (
    AcademicAuthor,
    AcademicIdentifiers,
    AcademicPaper,
    AcademicSearchInput,
    AcademicSearchResponse,
    AcademicSourceRecord,
    AcademicSourceStatus,
)
from server.tools.academic.normalize import (
    are_papers_duplicate,
    canonical_acl_landing_url,
    canonical_arxiv_landing_url,
    canonical_arxiv_pdf_url,
    canonical_doi_landing_url,
    is_arxiv_doi,
    merge_papers,
    normalize_doi,
    normalize_title,
    parse_arxiv_id,
    select_canonical_landing_url,
)


def test_academic_search_input_validation():
    # Valid input
    valid = AcademicSearchInput(query="Attention Is All You Need", max_results=5)
    assert valid.query == "Attention Is All You Need"
    assert valid.max_results == 5
    assert valid.sort == "relevance"
    assert valid.sources == []

    # Rejects too short query
    with pytest.raises(ValidationError):
        AcademicSearchInput(query="a")

    # Rejects empty query
    with pytest.raises(ValidationError):
        AcademicSearchInput(query="")

    # Rejects whitespace-only query
    with pytest.raises(ValidationError, match="non-whitespace"):
        AcademicSearchInput(query="   ")

    # Rejects query with only 1 non-whitespace char
    with pytest.raises(ValidationError, match="non-whitespace"):
        AcademicSearchInput(query="  a  ")

    # Whitespace-only query_en converts to None
    en_clean = AcademicSearchInput(query="transformer", query_en="   ")
    assert en_clean.query_en is None

    # Valid query_en strips whitespace
    en_ok = AcademicSearchInput(query="transformer", query_en="  Attention  ")
    assert en_ok.query_en == "Attention"

    # Rejects year_from > year_to
    with pytest.raises(ValidationError, match="cannot exceed year_to"):
        AcademicSearchInput(query="transformer", year_from=2025, year_to=2020)

    # Rejects too long query (> 500 chars)
    with pytest.raises(ValidationError):
        AcademicSearchInput(query="x" * 501)

    # Rejects max_results > 10
    with pytest.raises(ValidationError):
        AcademicSearchInput(query="transformer", max_results=11)

    # Rejects max_results < 1
    with pytest.raises(ValidationError):
        AcademicSearchInput(query="transformer", max_results=0)

    # Rejects out of bound years
    with pytest.raises(ValidationError):
        AcademicSearchInput(query="transformer", year_from=1899)
    with pytest.raises(ValidationError):
        AcademicSearchInput(query="transformer", year_to=2101)

    # Accepts valid year bounds
    bound_ok = AcademicSearchInput(query="transformer", year_from=1900, year_to=2100)
    assert bound_ok.year_from == 1900
    assert bound_ok.year_to == 2100


def test_academic_paper_and_identifiers_defaults():
    identifiers = AcademicIdentifiers(arxiv_id="1706.03762")
    assert identifiers.arxiv_id == "1706.03762"
    assert identifiers.doi is None
    assert identifiers.acl_id is None
    assert identifiers.semantic_scholar_id is None
    assert identifiers.corpus_id is None

    now = datetime.now(timezone.utc)
    paper = AcademicPaper(
        record_id="arxiv:1706.03762",
        title="Attention Is All You Need",
        authors=[AcademicAuthor(name="Ashish Vaswani")],
        identifiers=identifiers,
        landing_url="https://arxiv.org/abs/1706.03762",
        source_records=[
            AcademicSourceRecord(
                source="arxiv",
                source_id="1706.03762v7",
                source_url="https://arxiv.org/abs/1706.03762v7",
                retrieved_at=now,
            )
        ],
    )
    assert paper.publication_status == "unknown"
    assert paper.abstract is None
    assert paper.published_at is None
    assert paper.publication_year is None
    assert paper.first_submitted_at is None
    assert paper.updated_at is None
    assert paper.venue is None
    assert paper.pdf_url is None
    assert paper.scholar_url is None
    assert paper.integrity_status == "unknown"

    # Serialization roundtrip
    data_json = paper.model_dump_json()
    reconstructed = AcademicPaper.model_validate_json(data_json)
    assert reconstructed.record_id == paper.record_id
    assert str(reconstructed.landing_url) == "https://arxiv.org/abs/1706.03762"


def test_academic_search_response_serialization():
    now = datetime.now(timezone.utc)
    response = AcademicSearchResponse(
        query="Transformer",
        query_used="Transformer",
        papers=[],
        source_status=[
            AcademicSourceStatus(
                source="arxiv",
                status="ok",
            )
        ],
        partial=False,
        retrieved_at=now,
    )
    json_str = response.model_dump_json()
    loaded = AcademicSearchResponse.model_validate_json(json_str)
    assert loaded.query == "Transformer"
    assert loaded.partial is False
    assert len(loaded.source_status) == 1
    assert loaded.source_status[0].status == "ok"


def test_title_normalization():
    assert (
        normalize_title("  Attention Is All You Need!  ") == "attention is all you need"
    )
    assert normalize_title("“LoRA: Low-Rank Adaptation”") == "lora: low-rank adaptation"
    assert normalize_title("Deep  Residual   Learning...") == "deep residual learning"
    assert normalize_title("") == ""


def test_parse_arxiv_id():
    assert parse_arxiv_id("http://arxiv.org/abs/1706.03762v7") == (
        "1706.03762",
        "1706.03762v7",
    )
    assert parse_arxiv_id("https://arxiv.org/abs/2106.09685") == (
        "2106.09685",
        "2106.09685",
    )
    assert parse_arxiv_id("arxiv:1706.03762v1") == ("1706.03762", "1706.03762v1")
    assert parse_arxiv_id("1706.03762") == ("1706.03762", "1706.03762")
    assert parse_arxiv_id("math/0303109v2") == ("math/0303109", "math/0303109v2")

    with pytest.raises(ValueError, match="cannot parse arXiv identifier"):
        parse_arxiv_id("not-an-arxiv-id")

    with pytest.raises(ValueError, match="cannot parse arXiv identifier"):
        parse_arxiv_id("evil-prefix-1706.037621234-tail")

    with pytest.raises(ValueError, match="cannot parse arXiv identifier"):
        parse_arxiv_id("1706.03762 extra")


def test_canonical_urls():
    assert (
        canonical_arxiv_landing_url("http://arxiv.org/abs/1706.03762v7")
        == "https://arxiv.org/abs/1706.03762"
    )
    assert (
        canonical_arxiv_pdf_url("http://arxiv.org/abs/1706.03762v7")
        == "https://arxiv.org/pdf/1706.03762.pdf"
    )
    assert (
        canonical_acl_landing_url("P17-1001/") == "https://aclanthology.org/P17-1001/"
    )
    assert (
        canonical_doi_landing_url("https://doi.org/10.1145/3442188.3445922")
        == "https://doi.org/10.1145/3442188.3445922"
    )
    assert (
        canonical_doi_landing_url("doi:10.1016/j.neucom.2023.126575")
        == "https://doi.org/10.1016/j.neucom.2023.126575"
    )


def test_select_canonical_landing_url_priority():
    # 1. ACL ID takes top priority
    ids_with_acl = AcademicIdentifiers(
        acl_id="2020.acl-main.1",
        doi="10.18653/v1/2020.acl-main.1",
        arxiv_id="2005.14165",
    )
    assert (
        select_canonical_landing_url(ids_with_acl, "https://example.com")
        == "https://aclanthology.org/2020.acl-main.1/"
    )

    # 2. Formal publication DOI takes priority over arXiv
    ids_with_doi = AcademicIdentifiers(
        doi="10.1145/3442188.3445922",
        arxiv_id="2101.00001",
    )
    assert (
        select_canonical_landing_url(ids_with_doi, "https://example.com")
        == "https://doi.org/10.1145/3442188.3445922"
    )

    # 3. arXiv DataCite DOI routes to arXiv abstract
    ids_arxiv_doi = AcademicIdentifiers(
        doi="10.48550/arXiv.1706.03762",
        arxiv_id="1706.03762",
    )
    assert (
        select_canonical_landing_url(ids_arxiv_doi, "https://example.com")
        == "https://arxiv.org/abs/1706.03762"
    )

    # 4. Fallback URL when no identifiers
    ids_empty = AcademicIdentifiers()
    assert (
        select_canonical_landing_url(ids_empty, "https://fallback.example/paper")
        == "https://fallback.example/paper"
    )


def test_are_papers_duplicate_rules():
    base = AcademicPaper(
        record_id="rec1",
        title="Attention Is All You Need",
        authors=[AcademicAuthor(name="Ashish Vaswani")],
        identifiers=AcademicIdentifiers(
            doi="10.5555/3295222.3295349", arxiv_id="1706.03762"
        ),
        landing_url="https://arxiv.org/abs/1706.03762",
        source_records=[],
    )

    # Duplicate by DOI
    dup_doi = AcademicPaper(
        record_id="rec2",
        title="Different Title String",
        authors=[],
        identifiers=AcademicIdentifiers(doi="https://doi.org/10.5555/3295222.3295349"),
        landing_url="https://example.com",
        source_records=[],
    )
    assert are_papers_duplicate(base, dup_doi) is True

    # Duplicate by arXiv ID
    dup_arxiv = AcademicPaper(
        record_id="rec3",
        title="Another Title",
        authors=[],
        identifiers=AcademicIdentifiers(arxiv_id="1706.03762"),
        landing_url="https://example.com",
        source_records=[],
    )
    assert are_papers_duplicate(base, dup_arxiv) is True

    # Duplicate by ACL ID
    base_acl = AcademicPaper(
        record_id="rec_acl",
        title="BERT",
        authors=[],
        identifiers=AcademicIdentifiers(acl_id="N19-1423"),
        landing_url="https://example.com",
        source_records=[],
    )
    dup_acl = AcademicPaper(
        record_id="rec_acl_dup",
        title="BERT Pre-training",
        authors=[],
        identifiers=AcademicIdentifiers(acl_id="n19-1423/"),
        landing_url="https://example.com",
        source_records=[],
    )
    assert are_papers_duplicate(base_acl, dup_acl) is True

    # Not duplicate when title is different and IDs do not match
    diff_paper = AcademicPaper(
        record_id="rec4",
        title="Deep Residual Learning",
        authors=[],
        identifiers=AcademicIdentifiers(arxiv_id="1512.03385"),
        landing_url="https://example.com",
        source_records=[],
    )
    assert are_papers_duplicate(base, diff_paper) is False


def test_merge_papers_conflict_warning():
    now = datetime.now(timezone.utc)
    p1 = AcademicPaper(
        record_id="p1",
        title="Sample Paper",
        authors=[AcademicAuthor(name="Alice")],
        identifiers=AcademicIdentifiers(doi="10.1000/doi1"),
        landing_url="https://doi.org/10.1000/doi1",
        source_records=[
            AcademicSourceRecord(source="arxiv", source_id="s1", retrieved_at=now)
        ],
    )
    p2 = AcademicPaper(
        record_id="p2",
        title="Sample Paper",
        authors=[AcademicAuthor(name="Bob")],
        identifiers=AcademicIdentifiers(doi="10.1000/doi2"),
        landing_url="https://doi.org/10.1000/doi2",
        source_records=[
            AcademicSourceRecord(
                source="semantic_scholar", source_id="s2", retrieved_at=now
            )
        ],
    )

    merged, warnings = merge_papers(p1, p2)
    assert len(merged.authors) == 2
    assert any("DOI conflict" in w for w in warnings)
    assert merged.identifiers.doi == "10.1000/doi1"
    assert len(merged.source_records) == 2


def test_normalize_doi():
    assert (
        normalize_doi("https://doi.org/10.1145/3442188.3445922")
        == "10.1145/3442188.3445922"
    )
    assert (
        normalize_doi("http://dx.doi.org/10.1145/3442188.3445922/")
        == "10.1145/3442188.3445922"
    )
    assert (
        normalize_doi("doi:10.1016/j.neucom.2023.126575")
        == "10.1016/j.neucom.2023.126575"
    )
    assert normalize_doi("10.48550/arXiv.2106.09685") == "10.48550/arxiv.2106.09685"
    assert is_arxiv_doi("10.48550/arXiv.2106.09685") is True
    assert is_arxiv_doi("10.1145/3442188.3445922") is False
    assert is_arxiv_doi("10.48550/arxivistic.123") is False
    assert normalize_doi("") is None
    assert normalize_doi(None) is None


def test_are_papers_duplicate_missing_year_not_duplicate():
    # 1. Missing year on both papers: same title must NOT be considered duplicate
    p1 = AcademicPaper(
        record_id="p1",
        title="Attention Is All You Need",
        authors=[],
        identifiers=AcademicIdentifiers(),
        landing_url="https://example.com/1",
        source_records=[],
    )
    p2 = AcademicPaper(
        record_id="p2",
        title="attention is all you need",
        authors=[],
        identifiers=AcademicIdentifiers(),
        landing_url="https://example.com/2",
        source_records=[],
    )
    assert are_papers_duplicate(p1, p2) is False

    # 2. Missing year on one paper: must NOT be considered duplicate
    p3 = AcademicPaper(
        record_id="p3",
        title="Attention Is All You Need",
        authors=[],
        first_submitted_at=datetime(2017, 6, 12, tzinfo=timezone.utc),
        identifiers=AcademicIdentifiers(),
        landing_url="https://example.com/3",
        source_records=[],
    )
    assert are_papers_duplicate(p1, p3) is False

    # 3. Both have years within 1 year: duplicate matches
    p4 = AcademicPaper(
        record_id="p4",
        title="Attention Is All You Need",
        authors=[],
        published_at=datetime(2018, 1, 1, tzinfo=timezone.utc),
        identifiers=AcademicIdentifiers(),
        landing_url="https://example.com/4",
        source_records=[],
    )
    assert are_papers_duplicate(p3, p4) is True

    # 4. Both have years with difference > 1: NOT duplicate
    p5 = AcademicPaper(
        record_id="p5",
        title="Attention Is All You Need",
        authors=[],
        published_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        identifiers=AcademicIdentifiers(),
        landing_url="https://example.com/5",
        source_records=[],
    )
    assert are_papers_duplicate(p3, p5) is False


def test_merge_papers_same_doi_different_format_no_spurious_conflict():
    # One has bare DOI, the other has https://doi.org/...
    now = datetime.now(timezone.utc)
    p1 = AcademicPaper(
        record_id="p1",
        title="LoRA: Low-Rank Adaptation of Large Language Models",
        authors=[AcademicAuthor(name="Edward J. Hu")],
        identifiers=AcademicIdentifiers(doi="10.48550/arxiv.2106.09685"),
        landing_url="https://arxiv.org/abs/2106.09685",
        source_records=[
            AcademicSourceRecord(source="arxiv", source_id="s1", retrieved_at=now)
        ],
    )
    p2 = AcademicPaper(
        record_id="p2",
        title="LoRA: Low-Rank Adaptation of Large Language Models",
        authors=[AcademicAuthor(name="Edward J. Hu")],
        identifiers=AcademicIdentifiers(
            doi="https://doi.org/10.48550/arXiv.2106.09685"
        ),
        landing_url="https://arxiv.org/abs/2106.09685",
        source_records=[
            AcademicSourceRecord(
                source="semantic_scholar", source_id="s2", retrieved_at=now
            )
        ],
    )

    merged, warnings = merge_papers(p1, p2)
    # Must NOT produce DOI conflict warning
    assert not any("DOI conflict" in w for w in warnings)
    assert merged.identifiers.doi == "10.48550/arxiv.2106.09685"


def test_merge_papers_preprint_doi_remains_preprint_and_published_at_none():
    now = datetime.now(timezone.utc)
    p1 = AcademicPaper(
        record_id="arxiv:2106.09685",
        title="LoRA: Low-Rank Adaptation of Large Language Models",
        authors=[AcademicAuthor(name="Edward J. Hu")],
        first_submitted_at=datetime(2021, 6, 17, tzinfo=timezone.utc),
        publication_status="preprint",
        identifiers=AcademicIdentifiers(arxiv_id="2106.09685"),
        landing_url="https://arxiv.org/abs/2106.09685",
        source_records=[
            AcademicSourceRecord(
                source="arxiv", source_id="2106.09685v2", retrieved_at=now
            )
        ],
    )
    p2 = AcademicPaper(
        record_id="s2:lora",
        title="LoRA: Low-Rank Adaptation of Large Language Models",
        authors=[AcademicAuthor(name="Edward J. Hu", author_id="123")],
        first_submitted_at=datetime(2021, 6, 17, tzinfo=timezone.utc),
        publication_status="preprint",
        venue="ICLR",
        identifiers=AcademicIdentifiers(
            arxiv_id="2106.09685",
            doi="10.48550/arXiv.2106.09685",
            semantic_scholar_id="lora",
        ),
        landing_url="https://arxiv.org/abs/2106.09685",
        source_records=[
            AcademicSourceRecord(
                source="semantic_scholar", source_id="lora", retrieved_at=now
            )
        ],
    )

    merged, warnings = merge_papers(p1, p2)
    assert merged.publication_status == "preprint"
    assert merged.published_at is None
    assert merged.first_submitted_at == datetime(2021, 6, 17, tzinfo=timezone.utc)
    # Landing URL must route to arXiv abs page, not raw DataCite DOI URL
    assert str(merged.landing_url) == "https://arxiv.org/abs/2106.09685"


def test_merge_prefers_formal_doi_over_arxiv_datacite_doi():
    preprint = AcademicPaper(
        record_id="arxiv:2101.00001",
        title="Same Work",
        authors=[],
        first_submitted_at=datetime(2021, 1, 1, tzinfo=timezone.utc),
        publication_status="preprint",
        identifiers=AcademicIdentifiers(
            arxiv_id="2101.00001",
            doi="10.48550/arXiv.2101.00001",
        ),
        landing_url="https://arxiv.org/abs/2101.00001",
        source_records=[],
    )
    formal = AcademicPaper(
        record_id="s2:formal",
        title="Same Work",
        authors=[],
        published_at=datetime(2022, 1, 1, tzinfo=timezone.utc),
        publication_year=2022,
        publication_status="peer_reviewed",
        identifiers=AcademicIdentifiers(
            arxiv_id="2101.00001",
            doi="10.1145/1234567",
        ),
        landing_url="https://doi.org/10.1145/1234567",
        source_records=[],
    )

    merged, warnings = merge_papers(preprint, formal)

    assert merged.identifiers.doi == "10.1145/1234567"
    assert merged.publication_status == "peer_reviewed"
    assert merged.published_at == datetime(2022, 1, 1, tzinfo=timezone.utc)
    assert merged.first_submitted_at == datetime(2021, 1, 1, tzinfo=timezone.utc)
    assert merged.publication_year == 2022
    assert str(merged.landing_url) == "https://doi.org/10.1145/1234567"
    assert any("DOI conflict" in warning for warning in warnings)


def test_multiple_arxiv_versions_share_one_canonical_identity():
    canonical_v1, versioned_v1 = parse_arxiv_id(
        "https://arxiv.org/abs/1706.03762v1"
    )
    canonical_v7, versioned_v7 = parse_arxiv_id(
        "https://arxiv.org/pdf/1706.03762v7.pdf"
    )
    assert canonical_v1 == canonical_v7 == "1706.03762"
    assert versioned_v1 == "1706.03762v1"
    assert versioned_v7 == "1706.03762v7"

    first = AcademicPaper(
        record_id="arxiv:1706.03762",
        title="Attention Is All You Need",
        authors=[],
        publication_year=2017,
        identifiers=AcademicIdentifiers(arxiv_id=canonical_v1),
        landing_url="https://arxiv.org/abs/1706.03762",
        source_records=[],
    )
    later = first.model_copy(
        update={
            "record_id": "s2:transformer",
            "identifiers": AcademicIdentifiers(arxiv_id=canonical_v7),
        }
    )
    assert are_papers_duplicate(first, later) is True


def test_author_name_variants_are_merged_without_duplication():
    now = datetime.now(timezone.utc)
    first = AcademicPaper(
        record_id="one",
        title="A Stable Paper",
        authors=[AcademicAuthor(name="Jane Q. Doe")],
        publication_year=2024,
        identifiers=AcademicIdentifiers(doi="10.1000/stable"),
        landing_url="https://doi.org/10.1000/stable",
        source_records=[
            AcademicSourceRecord(source="arxiv", source_id="one", retrieved_at=now)
        ],
    )
    second = AcademicPaper(
        record_id="two",
        title="A Stable Paper",
        authors=[AcademicAuthor(name="  jane q. doe  ", author_id="author-1")],
        publication_year=2024,
        identifiers=AcademicIdentifiers(doi="10.1000/stable"),
        landing_url="https://doi.org/10.1000/stable",
        source_records=[
            AcademicSourceRecord(
                source="semantic_scholar", source_id="two", retrieved_at=now
            )
        ],
    )

    merged, _warnings = merge_papers(first, second)

    assert len(merged.authors) == 1
    assert merged.authors[0].name == "Jane Q. Doe"
    assert merged.authors[0].author_id == "author-1"


def test_integrity_status_keeps_most_severe_verified_state():
    active = AcademicPaper(
        record_id="one",
        title="Retracted Work",
        authors=[],
        publication_year=2020,
        integrity_status="active",
        identifiers=AcademicIdentifiers(doi="10.1000/retracted"),
        landing_url="https://doi.org/10.1000/retracted",
        source_records=[],
    )
    retracted = active.model_copy(
        update={"record_id": "two", "integrity_status": "retracted"}
    )

    merged, _warnings = merge_papers(active, retracted)

    assert merged.integrity_status == "retracted"


def test_formal_publication_date_conflict_is_visible_and_deterministic():
    first = AcademicPaper(
        record_id="one",
        title="Published Work",
        authors=[],
        published_at=datetime(2022, 6, 1, tzinfo=timezone.utc),
        publication_status="peer_reviewed",
        identifiers=AcademicIdentifiers(doi="10.1000/published"),
        landing_url="https://doi.org/10.1000/published",
        source_records=[],
    )
    second = first.model_copy(
        update={
            "record_id": "two",
            "published_at": datetime(2022, 1, 15, tzinfo=timezone.utc),
        }
    )

    merged, warnings = merge_papers(first, second)

    assert merged.published_at == datetime(2022, 1, 15, tzinfo=timezone.utc)
    assert any("publication date conflict" in warning for warning in warnings)
