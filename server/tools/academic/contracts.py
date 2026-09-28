"""Strict Pydantic data contracts for academic search."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


AcademicSource = Literal["arxiv", "semantic_scholar", "crossref", "acl"]


class AcademicSearchInput(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    query_en: str | None = Field(default=None, max_length=500)
    sources: list[AcademicSource] = Field(default_factory=list)
    max_results: int = Field(default=5, ge=1, le=10)
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)
    sort: Literal["relevance", "recent"] = "relevance"

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < 2:
            raise ValueError("query must contain at least 2 non-whitespace characters")
        if len(stripped) > 500:
            raise ValueError("query cannot exceed 500 characters")
        return stripped

    @field_validator("query_en")
    @classmethod
    def validate_query_en(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if len(stripped) > 500:
            raise ValueError("query_en cannot exceed 500 characters")
        return stripped

    @model_validator(mode="after")
    def validate_year_range(self) -> "AcademicSearchInput":
        if self.year_from is not None and self.year_to is not None:
            if self.year_from > self.year_to:
                raise ValueError(
                    f"year_from ({self.year_from}) cannot exceed year_to ({self.year_to})"
                )
        return self


class AcademicAuthor(BaseModel):
    name: str
    author_id: str | None = None


class AcademicIdentifiers(BaseModel):
    doi: str | None = None
    arxiv_id: str | None = None
    acl_id: str | None = None
    semantic_scholar_id: str | None = None
    corpus_id: str | None = None


class AcademicSourceRecord(BaseModel):
    source: AcademicSource
    source_id: str
    source_url: HttpUrl | None = None
    rank: int | None = Field(default=None, ge=1)
    retrieved_at: datetime


class AcademicPaper(BaseModel):
    record_id: str
    title: str
    authors: list[AcademicAuthor]
    abstract: str | None = None
    first_submitted_at: datetime | None = None
    updated_at: datetime | None = None
    published_at: datetime | None = None
    publication_year: int | None = Field(default=None, ge=1000, le=2100)
    publication_status: Literal[
        "preprint", "peer_reviewed", "technical_report", "unknown"
    ] = "unknown"
    venue: str | None = None
    identifiers: AcademicIdentifiers
    landing_url: HttpUrl
    pdf_url: HttpUrl | None = None
    scholar_url: HttpUrl | None = None
    integrity_status: Literal["active", "withdrawn", "retracted", "unknown"] = (
        "unknown"
    )
    source_records: list[AcademicSourceRecord]


class AcademicSourceStatus(BaseModel):
    source: AcademicSource
    status: Literal[
        "ok", "skipped", "timeout", "rate_limited", "circuit_open", "error"
    ]
    message: str = ""


class AcademicSearchResponse(BaseModel):
    query: str
    query_used: str
    papers: list[AcademicPaper]
    source_status: list[AcademicSourceStatus]
    partial: bool
    warnings: list[str] = Field(default_factory=list)
    retrieved_at: datetime
