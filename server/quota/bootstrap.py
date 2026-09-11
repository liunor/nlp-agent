"""Database pricing bootstrap and production coverage validation."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import Engine, select

from configs.settings import settings
from core.model_runtime.contracts import ModelRuntimeConfig
from core.model_runtime.reporters import configure_global_model_usage_reporter
from server.quota.builtin_pricing import (
    BUILTIN_PRICING_CREATED_BY,
    BUILTIN_PRICING_RULES,
    CREDITS_MICRO_PER_CNY,
    UNVERIFIED_PRICING_KEYS,
)
from server.quota.errors import QuotaDomainError, UsageReporterConfigurationError
from server.quota.management import QuotaManagementService
from server.quota.models import PricingRuleModel
from server.quota.reporting import DurableModelUsageReporter
from server.quota.service import QuotaService


UTC = timezone.utc


def install_builtin_pricing_catalog(
    database: str | Engine,
    *,
    installed_by: str = BUILTIN_PRICING_CREATED_BY,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Install every built-in rule and retain a result for every pricing key."""
    installed_at = now or datetime.now(UTC)
    service = QuotaManagementService(database)
    results: list[dict[str, Any]] = []
    try:
        for definition in BUILTIN_PRICING_RULES:
            try:
                result = service.install_builtin_pricing_version(
                    definition,
                    installed_by=installed_by,
                    now=installed_at,
                )
            except QuotaDomainError as error:
                results.append(
                    {
                        "pricing_key": definition.pricing_key,
                        "version": definition.version,
                        "status": "conflict",
                        "error_code": error.code.value,
                        "message": str(error),
                    }
                )
                continue
            results.append(
                {
                    "pricing_key": result["pricing_key"],
                    "version": result["version"],
                    "status": result["install_status"],
                    "effective_from": result["effective_from"],
                    "superseded_rule_id": result["superseded_rule_id"],
                }
            )
    finally:
        service.close()
    return {
        "currency": "CNY",
        "credits_micro_per_cny": CREDITS_MICRO_PER_CNY,
        "rules": results,
        "unverified_pricing_keys": list(UNVERIFIED_PRICING_KEYS),
    }


def pricing_requirements(
    config: ModelRuntimeConfig,
    runtime_config: Mapping[str, Any],
) -> dict[str, frozenset[str]]:
    """Return reachable pricing keys and any feature rates they require."""
    required: dict[str, set[str]] = defaultdict(set)
    for preset in config.model_presets.values():
        model = config.models[preset.model]
        if model.pricing_key:
            required[model.pricing_key]
            if preset.native_search.enabled:
                required[model.pricing_key].add("search_call_credits_micro")

    tools = runtime_config.get("tools", {})
    if not isinstance(tools, Mapping):
        tools = {}
    vision = tools.get("vision", {})
    if isinstance(vision, Mapping) and bool(vision.get("enabled", True)):
        required["feature/image-understanding"].add("image_unit_credits_micro")
        route = config.model_routes.get("vision-worker")
        if route is not None:
            preset = config.model_presets[route.primary]
            model = config.models[preset.model]
            if model.pricing_key:
                required[model.pricing_key].update(
                    {
                        "visual_input_credits_micro_per_million_tokens",
                        "image_unit_credits_micro",
                    }
                )
    web = tools.get("web", {})
    if isinstance(web, Mapping) and bool(web.get("enabled", False)):
        required["feature/link-read"].add("link_page_credits_micro")
    return {key: frozenset(fields) for key, fields in required.items()}


def validate_pricing_coverage(
    database: str | Engine,
    *,
    config: ModelRuntimeConfig,
    runtime_config: Mapping[str, Any],
    at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return missing or ambiguous production pricing requirements."""
    checked_at = (at or datetime.now(UTC)).astimezone(UTC)
    stored_at = checked_at.replace(tzinfo=None)
    requirements = pricing_requirements(config, runtime_config)
    service = QuotaManagementService(database)
    issues: list[dict[str, Any]] = []
    try:
        with service.engine.connect() as connection:
            for pricing_key, required_fields in sorted(requirements.items()):
                rows = connection.execute(
                    select(PricingRuleModel).where(
                        PricingRuleModel.pricing_key == pricing_key,
                        PricingRuleModel.status == "active",
                        PricingRuleModel.effective_from <= stored_at,
                        (PricingRuleModel.effective_until.is_(None))
                        | (PricingRuleModel.effective_until > stored_at),
                    )
                ).mappings().all()
                if len(rows) != 1:
                    issues.append(
                        {
                            "pricing_key": pricing_key,
                            "reason": "missing" if not rows else "ambiguous",
                            "active_rule_count": len(rows),
                        }
                    )
                    continue
                missing_fields = sorted(
                    field for field in required_fields if rows[0][field] is None
                )
                if missing_fields:
                    issues.append(
                        {
                            "pricing_key": pricing_key,
                            "reason": "missing_feature_rates",
                            "fields": missing_fields,
                        }
                    )
    finally:
        service.close()
    for pricing_key in UNVERIFIED_PRICING_KEYS:
        issues.append({"pricing_key": pricing_key, "reason": "unverified"})
    return issues


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
                "Durable usage Reporter is required but "
                "NLP_AGENT_DATABASE_URL is not configured"
            )
        return None
    quota_service = QuotaService(resolved) if quota_enforcement else None
    reporter: DurableModelUsageReporter | None = None
    try:
        if quota_service is not None:
            quota_service.verify_schema()
        reporter = DurableModelUsageReporter(resolved, quota_service=quota_service)
        if required:
            # Usage reporting is enabled in the production web/worker paths
            # even when quota enforcement is disabled. Probe its complete
            # table contract before accepting model traffic.
            reporter.verify_schema()
    except Exception:
        if reporter is not None:
            reporter.close()
        elif quota_service is not None:
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
