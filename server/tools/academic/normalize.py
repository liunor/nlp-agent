"""Title normalization and academic identifier parsing."""

from __future__ import annotations

import re
import string
import unicodedata
from urllib.parse import urlencode

from server.tools.academic.contracts import (
    AcademicAuthor,
    AcademicIdentifiers,
    AcademicPaper,
    AcademicSourceRecord,
)

_PUNCTUATION_SET = set(string.punctuation)
_ARXIV_ID_PATTERN = re.compile(
    r"^(?:https?://arxiv\.org/(?:abs|pdf)/|arxiv:)?([a-z\-]+(?:\.[a-z\-]+)?/\d{7}|\d{4}\.\d{4,5})(?:v(\d+))?(?:\.pdf)?/?$",
    re.IGNORECASE,
)


def normalize_title(title: str) -> str:
    """Normalize paper title for matching while preserving the original for display.

    Applies:
    1. Unicode NFKC normalization
    2. Lowercasing
    3. Collapsing multiple whitespace characters to a single space
    4. Stripping leading and trailing punctuation (ASCII and Unicode)
    """
    if not title:
        return ""
    normalized = unicodedata.normalize("NFKC", title)
    normalized = normalized.lower()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    while normalized and (
        unicodedata.category(normalized[0]).startswith("P")
        or normalized[0] in _PUNCTUATION_SET
    ):
        normalized = normalized[1:]
    while normalized and (
        unicodedata.category(normalized[-1]).startswith("P")
        or normalized[-1] in _PUNCTUATION_SET
    ):
        normalized = normalized[:-1]
    return normalized.strip()


def google_scholar_verification_url(title: str) -> str:
    """Create a manual verification URL; Scholar is never a metadata source."""
    return "https://scholar.google.com/scholar?" + urlencode({"q": f'"{title}"'})


def parse_arxiv_id(raw: str) -> tuple[str, str]:
    """Extract canonical (unversioned) and versioned arXiv IDs.

    Returns:
        (canonical_id, versioned_id). If no version was in raw, versioned_id equals canonical_id.

    Raises:
        ValueError: If the raw string does not match an arXiv identifier pattern.
    """
    raw_clean = raw.strip()
    match = _ARXIV_ID_PATTERN.fullmatch(raw_clean)
    if not match:
        raise ValueError(f"cannot parse arXiv identifier from {raw!r}")
    canonical_id = match.group(1)
    version = match.group(2)
    versioned_id = f"{canonical_id}v{version}" if version else canonical_id
    return canonical_id, versioned_id


def canonical_arxiv_landing_url(arxiv_id: str) -> str:
    """Return standard user-facing HTTPS landing URL."""
    canonical, _ = parse_arxiv_id(arxiv_id)
    return f"https://arxiv.org/abs/{canonical}"


def canonical_arxiv_pdf_url(arxiv_id: str) -> str:
    """Return standard direct HTTPS PDF URL."""
    canonical, _ = parse_arxiv_id(arxiv_id)
    return f"https://arxiv.org/pdf/{canonical}.pdf"


def canonical_acl_landing_url(acl_id: str) -> str:
    """Return standard ACL Anthology landing URL."""
    clean_id = acl_id.strip().strip("/")
    return f"https://aclanthology.org/{clean_id}/"


def normalize_doi(raw: str | None) -> str | None:
    """Normalize DOI to canonical bare form (e.g. '10.1145/3442188.3445922')."""
    if not raw or not isinstance(raw, str):
        return None
    clean = raw.strip()
    clean = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"^doi:\s*", "", clean, flags=re.IGNORECASE).strip()
    clean = clean.strip("/")
    if not clean:
        return None
    return clean.lower()


def is_arxiv_doi(doi: str | None) -> bool:
    """Check if a DOI is an arXiv DataCite preprint DOI (10.48550/arXiv...)."""
    norm = normalize_doi(doi)
    return bool(norm and norm.startswith("10.48550/arxiv."))


def canonical_doi_landing_url(doi: str) -> str:
    """Return standard DOI landing URL."""
    norm = normalize_doi(doi)
    return f"https://doi.org/{norm}" if norm else f"https://doi.org/{doi.strip()}"


def select_canonical_landing_url(
    identifiers: AcademicIdentifiers, fallback_url: str
) -> str:
    """Select landing URL following official precedence: ACL > formal DOI > arXiv > fallback."""
    if identifiers.acl_id:
        return canonical_acl_landing_url(identifiers.acl_id)

    # Formal publication DOI (exclude arXiv DataCite preprint DOIs)
    if identifiers.doi and not is_arxiv_doi(identifiers.doi):
        return canonical_doi_landing_url(identifiers.doi)

    if identifiers.arxiv_id:
        return canonical_arxiv_landing_url(identifiers.arxiv_id)

    if identifiers.doi:
        return canonical_doi_landing_url(identifiers.doi)

    return fallback_url


def _extract_paper_year(paper: AcademicPaper) -> int | None:
    if paper.published_at:
        return paper.published_at.year
    if paper.first_submitted_at:
        return paper.first_submitted_at.year
    return paper.publication_year


def _canonical_arxiv_id(raw: str | None) -> str | None:
    """Return a stable arXiv identity even if a caller supplies a version."""
    if not raw or not isinstance(raw, str):
        return None
    clean = raw.strip()
    if not clean:
        return None
    try:
        canonical, _ = parse_arxiv_id(clean)
    except ValueError:
        return clean.lower()
    return canonical.lower()


def are_papers_duplicate(p1: AcademicPaper, p2: AcademicPaper) -> bool:
    """Check if two papers represent the same work per Section 14.2 priority:
    1. Exact DOI match
    2. Exact arXiv ID match
    3. Exact ACL ID match
    4. Normalized title match AND year difference <= 1
    """
    # 1. DOI exact match
    d1 = normalize_doi(p1.identifiers.doi)
    d2 = normalize_doi(p2.identifiers.doi)
    if d1 and d2 and d1 == d2:
        return True

    # 2. arXiv ID exact match
    arxiv_1 = _canonical_arxiv_id(p1.identifiers.arxiv_id)
    arxiv_2 = _canonical_arxiv_id(p2.identifiers.arxiv_id)
    if arxiv_1 and arxiv_2:
        if arxiv_1 == arxiv_2:
            return True

    # 3. ACL ID exact match
    if p1.identifiers.acl_id and p2.identifiers.acl_id:
        a1 = p1.identifiers.acl_id.strip().strip("/").lower()
        a2 = p2.identifiers.acl_id.strip().strip("/").lower()
        if a1 and a2 and a1 == a2:
            return True

    # 4. Normalized title match and year difference <= 1
    t1 = normalize_title(p1.title)
    t2 = normalize_title(p2.title)
    if t1 and t2 and t1 == t2:
        y1 = _extract_paper_year(p1)
        y2 = _extract_paper_year(p2)
        if y1 is not None and y2 is not None and abs(y1 - y2) <= 1:
            return True
        return False

    return False


def merge_papers(
    primary: AcademicPaper, secondary: AcademicPaper
) -> tuple[AcademicPaper, list[str]]:
    """Merge duplicate paper metadata into a unified record preserving source tracking."""
    warnings: list[str] = []

    # 1. Authors union
    author_map: dict[str, AcademicAuthor] = {}
    for a in primary.authors:
        key = a.name.strip().lower()
        if key:
            author_map[key] = a
    for a in secondary.authors:
        key = a.name.strip().lower()
        if not key:
            continue
        if key not in author_map:
            author_map[key] = a
        elif not author_map[key].author_id and a.author_id:
            author_map[key] = AcademicAuthor(
                name=author_map[key].name, author_id=a.author_id
            )
    merged_authors = list(author_map.values())

    # 2. Identifiers union
    d1 = normalize_doi(primary.identifiers.doi)
    d2 = normalize_doi(secondary.identifiers.doi)
    if d1 and d2:
        if d1 != d2:
            warnings.append(
                f"DOI conflict between '{primary.identifiers.doi}' and '{secondary.identifiers.doi}'"
            )
            # A repository-issued preprint DOI must not hide a verified formal DOI.
            if is_arxiv_doi(d1) != is_arxiv_doi(d2):
                merged_doi = d2 if is_arxiv_doi(d1) else d1
            else:
                merged_doi = d1
        else:
            merged_doi = d1
    else:
        merged_doi = d1 or d2

    primary_arxiv = _canonical_arxiv_id(primary.identifiers.arxiv_id)
    secondary_arxiv = _canonical_arxiv_id(secondary.identifiers.arxiv_id)
    merged_arxiv = primary_arxiv or secondary_arxiv
    if primary_arxiv and secondary_arxiv:
        if primary_arxiv != secondary_arxiv:
            warnings.append(
                f"arXiv ID conflict between '{primary.identifiers.arxiv_id}' and '{secondary.identifiers.arxiv_id}'"
            )

    merged_acl = primary.identifiers.acl_id or secondary.identifiers.acl_id
    if primary.identifiers.acl_id and secondary.identifiers.acl_id:
        acl_1 = primary.identifiers.acl_id.strip().strip("/").lower()
        acl_2 = secondary.identifiers.acl_id.strip().strip("/").lower()
        if acl_1 != acl_2:
            warnings.append(
                f"ACL ID conflict between '{primary.identifiers.acl_id}' and '{secondary.identifiers.acl_id}'"
            )
    merged_s2 = (
        primary.identifiers.semantic_scholar_id
        or secondary.identifiers.semantic_scholar_id
    )
    merged_corpus = primary.identifiers.corpus_id or secondary.identifiers.corpus_id
    if (
        primary.identifiers.corpus_id
        and secondary.identifiers.corpus_id
        and primary.identifiers.corpus_id != secondary.identifiers.corpus_id
    ):
        warnings.append(
            "Semantic Scholar CorpusId conflict between "
            f"'{primary.identifiers.corpus_id}' and '{secondary.identifiers.corpus_id}'"
        )

    merged_identifiers = AcademicIdentifiers(
        doi=merged_doi,
        arxiv_id=merged_arxiv,
        acl_id=merged_acl,
        semantic_scholar_id=merged_s2,
        corpus_id=merged_corpus,
    )

    # 3. Publication status & Dates
    peer_reviewed_papers = [
        paper
        for paper in (primary, secondary)
        if paper.publication_status == "peer_reviewed"
        and (
            paper.identifiers.acl_id
            or (paper.identifiers.doi and not is_arxiv_doi(paper.identifiers.doi))
        )
    ]
    first_submitted_dates = [
        paper.first_submitted_at
        for paper in (primary, secondary)
        if paper.first_submitted_at is not None
    ]
    formal_publication_dates = [
        paper.published_at
        for paper in peer_reviewed_papers
        if paper.published_at is not None
    ]
    if len(set(formal_publication_dates)) > 1:
        warnings.append(
            "formal publication date conflict between "
            + " and ".join(date.date().isoformat() for date in formal_publication_dates)
        )

    if merged_acl or peer_reviewed_papers:
        merged_status = "peer_reviewed"
        merged_published_at = (
            min(formal_publication_dates) if formal_publication_dates else None
        )
        if merged_published_at is None and merged_acl:
            merged_published_at = primary.published_at or secondary.published_at
        merged_first_submitted_at = (
            min(first_submitted_dates) if first_submitted_dates else None
        )
    elif merged_doi and not is_arxiv_doi(merged_doi):
        # A formal DOI preserves publication metadata, but does not prove review.
        merged_status = "unknown"
        merged_published_at = primary.published_at or secondary.published_at
        merged_first_submitted_at = (
            min(first_submitted_dates) if first_submitted_dates else None
        )
    elif (
        primary.publication_status == "preprint"
        or secondary.publication_status == "preprint"
        or merged_arxiv
        or (merged_doi and is_arxiv_doi(merged_doi))
    ):
        merged_status = "preprint"
        merged_published_at = None
        candidate_submission_dates = first_submitted_dates or [
            date
            for date in (primary.published_at, secondary.published_at)
            if date is not None
        ]
        merged_first_submitted_at = (
            min(candidate_submission_dates) if candidate_submission_dates else None
        )
    elif (
        primary.publication_status == "technical_report"
        or secondary.publication_status == "technical_report"
    ):
        merged_status = "technical_report"
        merged_published_at = primary.published_at or secondary.published_at
        merged_first_submitted_at = (
            primary.first_submitted_at or secondary.first_submitted_at
        )
    else:
        merged_status = "unknown"
        merged_published_at = primary.published_at or secondary.published_at
        merged_first_submitted_at = (
            primary.first_submitted_at or secondary.first_submitted_at
        )

    updated_dates = [
        date for date in (primary.updated_at, secondary.updated_at) if date is not None
    ]
    merged_updated_at = max(updated_dates) if updated_dates else None
    merged_publication_year = (
        merged_published_at.year
        if merged_published_at is not None
        else merged_first_submitted_at.year
        if merged_first_submitted_at is not None
        else next(
            (
                paper.publication_year
                for paper in peer_reviewed_papers + [primary, secondary]
                if paper.publication_year is not None
            ),
            None,
        )
    )

    # 4. Abstract: keep richer / longer abstract
    if not primary.abstract and secondary.abstract:
        merged_abstract = secondary.abstract
    elif primary.abstract and secondary.abstract:
        merged_abstract = (
            primary.abstract
            if len(primary.abstract) >= len(secondary.abstract)
            else secondary.abstract
        )
    else:
        merged_abstract = primary.abstract

    # 5. Venue
    merged_venue = primary.venue or secondary.venue
    if (
        primary.venue
        and secondary.venue
        and primary.venue.strip().casefold() != secondary.venue.strip().casefold()
    ):
        warnings.append(
            f"venue conflict between '{primary.venue}' and '{secondary.venue}'"
        )

    # 6. Source records union
    seen_sources: set[tuple[str, str]] = set()
    merged_records: list[AcademicSourceRecord] = []
    for r in primary.source_records + secondary.source_records:
        k = (str(r.source), str(r.source_id))
        if k not in seen_sources:
            seen_sources.add(k)
            merged_records.append(r)

    # 7. Landing URL & PDF URL
    merged_landing_url = select_canonical_landing_url(
        merged_identifiers, str(primary.landing_url)
    )
    merged_pdf_url = primary.pdf_url or secondary.pdf_url
    if not merged_pdf_url and merged_arxiv:
        merged_pdf_url = canonical_arxiv_pdf_url(merged_arxiv)
    integrity_priority = {"unknown": 0, "active": 1, "withdrawn": 2, "retracted": 3}
    merged_integrity_status = max(
        (primary.integrity_status, secondary.integrity_status),
        key=integrity_priority.__getitem__,
    )

    merged_paper = AcademicPaper(
        record_id=primary.record_id,
        title=primary.title,
        authors=merged_authors,
        abstract=merged_abstract,
        first_submitted_at=merged_first_submitted_at,
        updated_at=merged_updated_at,
        published_at=merged_published_at,
        publication_year=merged_publication_year,
        publication_status=merged_status,
        venue=merged_venue,
        identifiers=merged_identifiers,
        landing_url=merged_landing_url,
        pdf_url=merged_pdf_url,
        scholar_url=primary.scholar_url or secondary.scholar_url,
        integrity_status=merged_integrity_status,
        source_records=merged_records,
    )
    return merged_paper, warnings


def deduplicate_and_merge_papers(
    papers: list[AcademicPaper],
) -> tuple[list[AcademicPaper], list[str]]:
    """Cluster duplicate papers and merge metadata, returning deduplicated papers and warnings."""
    merged_list: list[AcademicPaper] = []
    warnings: list[str] = []

    for paper in papers:
        matched_idx = -1
        for idx, existing in enumerate(merged_list):
            if are_papers_duplicate(existing, paper):
                matched_idx = idx
                break

        if matched_idx >= 0:
            merged_paper, warn = merge_papers(merged_list[matched_idx], paper)
            merged_list[matched_idx] = merged_paper
            warnings.extend(warn)
        else:
            merged_list.append(paper)

    return merged_list, warnings
