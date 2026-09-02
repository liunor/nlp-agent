"""Process-lifecycle wiring for the Runtime usage Reporter."""

from __future__ import annotations

from sqlalchemy import Engine

from configs.settings import settings
from core.model_runtime.reporters import configure_global_model_usage_reporter
from server.quota.errors import UsageReporterConfigurationError
from server.quota.reporting import DurableModelUsageReporter
from server.quota.service import QuotaService


def configure_usage_reporter(
    database: str | Engine | None = None,
    *,
    required: bool = False,
    quota_enforcement: bool = False,
) -> DurableModelUsageReporter | None:
    """Install the durable Reporter, optionally failing closed without a DB."""
    resolved = database
    if resolved is None:
        resolved = settings.NLP_AGENT_DATABASE_URL.strip()
    if resolved is None or (isinstance(resolved, str) and not resolved.strip()):
        if required:
            raise UsageReporterConfigurationError(
                "Durable usage Reporter is required but NLP_AGENT_DATABASE_URL is not configured"
            )
        return None
    quota_service = QuotaService(resolved) if quota_enforcement else None
    try:
        if quota_service is not None:
            quota_service.verify_schema()
        reporter = DurableModelUsageReporter(resolved, quota_service=quota_service)
    except Exception:
        if quota_service is not None:
            quota_service.close()
        raise
    if required:
        configure_global_model_usage_reporter(reporter, required=True)
    else:
        configure_global_model_usage_reporter(reporter)
    return reporter


def shutdown_usage_reporter(
    reporter: DurableModelUsageReporter | None,
) -> None:
    if reporter is None:
        return
    configure_global_model_usage_reporter(None)
    reporter.close()
