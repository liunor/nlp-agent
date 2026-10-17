"""Stable HTTP classifications for database saturation and lock failures."""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError, TimeoutError as SQLAlchemyTimeoutError


def database_problem(error: SQLAlchemyError) -> tuple[str, str, str | None]:
    """Return ``(code, title, retry_after)`` without exposing SQL details."""

    if isinstance(error, SQLAlchemyTimeoutError):
        return "database_busy", "Database connection pool is busy", "1"

    original = getattr(error, "orig", None)
    arguments = getattr(original, "args", ())
    error_number = arguments[0] if arguments else None
    message = str(original or error).lower()
    if error_number in {1205, 1213, 3024} or any(
        marker in message
        for marker in (
            "lock wait timeout",
            "deadlock",
            "maximum statement execution time exceeded",
            "max_statement_time exceeded",
        )
    ):
        return "database_timeout", "Database operation timed out", "1"

    return "database_unavailable", "Database is temporarily unavailable", "1"
