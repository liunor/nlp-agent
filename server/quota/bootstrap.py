"""Process-lifecycle wiring for the runtime usage reporter."""

from __future__ import annotations

from typing import TYPE_CHECKING
from sqlalchemy import Engine

from configs.settings import settings
from core.model_runtime.reporters import configure_global_model_usage_reporter
from server.quota.errors import UsageReporterConfigurationError
from server.quota.reporting import DurableModelUsageReporter

if TYPE_CHECKING:
    from server.quota.service import QuotaService


def configure_usage_reporter(
    database: str | Engine | None = None,
    *,
    required: bool = False,
    quota_enforcement: bool = False,
) -> DurableModelUsageReporter | None:
    """Install the durable reporter, optionally failing closed without a DB."""
    resolved = database
    if resolved is None:
        raw_url = getattr(settings, "NLP_AGENT_DATABASE_URL", "")
        resolved = raw_url.strip() if isinstance(raw_url, str) else ""

    if resolved is None or (isinstance(resolved, str) and not resolved.strip()):
        if required:
            raise UsageReporterConfigurationError(
                "Durable usage Reporter is required but NLP_AGENT_DATABASE_URL is not configured"
            )
        return None

    quota_service: QuotaService | None = None
    if quota_enforcement:
        try:
            from server.quota.service import QuotaService

            quota_service = QuotaService(resolved)
        except (ImportError, Exception):
            quota_service = None

    try:
        if quota_service is not None and hasattr(quota_service, "verify_schema"):
            quota_service.verify_schema()
        reporter = DurableModelUsageReporter(resolved, quota_service=quota_service)
    except Exception:
        if quota_service is not None and hasattr(quota_service, "close"):
            quota_service.close()
        raise

    configure_global_model_usage_reporter(reporter)
    return reporter


def shutdown_usage_reporter(
    reporter: DurableModelUsageReporter | None,
) -> None:
    """Safely reset global slot and release reporter database connections."""
    if reporter is None:
        return
    configure_global_model_usage_reporter(None)
    close_fn = getattr(reporter, "close", None)
    if callable(close_fn):
        close_fn()
