"""Protocol and exceptions for academic search providers."""

from __future__ import annotations

from typing import Protocol

from server.tools.academic.contracts import AcademicPaper, AcademicSearchInput


class AcademicProviderError(Exception):
    """Base exception for academic provider failures."""


class AcademicTimeoutError(AcademicProviderError):
    """Raised when an academic provider request times out."""


class AcademicRateLimitError(AcademicProviderError):
    """Raised when an academic provider returns 429 or rate limits."""

    def __init__(self, message: str = "rate limited", retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class AcademicResponseTooLargeError(AcademicProviderError):
    """Raised when the response size exceeds the configured safety threshold."""


class AcademicCircuitOpenError(AcademicProviderError):
    """Raised when a provider is temporarily rejected by its circuit breaker."""


class AcademicProvider(Protocol):
    name: str

    async def search(self, request: AcademicSearchInput) -> list[AcademicPaper]:
        ...
