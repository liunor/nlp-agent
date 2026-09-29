"""Academic search tool contracts, providers, and service."""

from server.tools.academic.contracts import (
    AcademicAuthor,
    AcademicIdentifiers,
    AcademicPaper,
    AcademicSearchInput,
    AcademicSearchResponse,
    AcademicSource,
    AcademicSourceRecord,
    AcademicSourceStatus,
)

__all__ = [
    "AcademicAuthor",
    "AcademicIdentifiers",
    "AcademicPaper",
    "AcademicSearchInput",
    "AcademicSearchResponse",
    "AcademicSource",
    "AcademicSourceRecord",
    "AcademicSourceStatus",
]
