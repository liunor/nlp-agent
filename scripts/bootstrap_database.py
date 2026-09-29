"""Schema migration plus deterministic pricing bootstrap for deployments."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from alembic import command
from alembic.config import Config

from configs.settings import BASE_DIR, settings
from core.model_runtime.contracts import ModelRuntimeConfig
from core.runtime_config import load_runtime_config
from server.quota.bootstrap import (
    install_builtin_pricing_catalog,
    validate_pricing_coverage,
)


UTC = timezone.utc


def _upgrade_schema(database_url: str) -> None:
    config = Config(str(BASE_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def bootstrap_database(
    *,
    database_url: str,
    runtime_config: Mapping[str, Any],
    quota_enforcement_enabled: bool,
    now: datetime | None = None,
    migrate: Callable[[str], None] = _upgrade_schema,
) -> tuple[dict[str, Any], bool]:
    """Run the deployment bootstrap and return its JSON-safe summary."""
    if not database_url.strip():
        raise ValueError("NLP_AGENT_DATABASE_URL is not configured")
    installed_at = (now or datetime.now(UTC)).astimezone(UTC)
    migrate(database_url)

    config = ModelRuntimeConfig.model_validate(
        {
            "providers": runtime_config.get("providers", {}),
            "models": runtime_config.get("models", {}),
            "model_presets": runtime_config.get("model_presets", {}),
            "model_routes": runtime_config.get("model_routes", {}),
            "model_profiles": runtime_config.get("model_profiles", {}),
            "default_model_profile": runtime_config.get("defaults", {}).get(
                "model_profile"
            ),
        }
    )
    installation = install_builtin_pricing_catalog(database_url, now=installed_at)
    grouped: dict[str, list[dict[str, Any]]] = {
        "created": [],
        "already_present": [],
        "superseded": [],
        "conflicts": [],
        "missing": [],
    }
    for result in installation["rules"]:
        status = result["status"]
        grouped["conflicts" if status == "conflict" else status].append(result)

    grouped["missing"] = validate_pricing_coverage(
        database_url,
        config=config,
        runtime_config=runtime_config,
        at=installed_at,
    )
    warnings: list[str] = []
    if grouped["missing"] and not quota_enforcement_enabled:
        warnings.append(
            "Pricing coverage is incomplete while quota enforcement is disabled; "
            "affected usage remains pending."
        )
    ambiguous = [
        issue for issue in grouped["missing"] if issue.get("reason") == "ambiguous"
    ]
    success = not grouped["conflicts"] and not ambiguous and not (
        quota_enforcement_enabled and grouped["missing"]
    )
    return (
        {
            "schema": "up_to_date",
            "pricing": grouped,
            "quota_enforcement_enabled": quota_enforcement_enabled,
            "warnings": warnings,
        },
        success,
    )


def run() -> None:
    try:
        summary, success = bootstrap_database(
            database_url=str(settings.database_runtime.get("url", "")),
            runtime_config=load_runtime_config(),
            quota_enforcement_enabled=settings.quota_enforcement_enabled,
        )
    except Exception as error:
        summary = {
            "schema": "failed",
            "pricing": {
                "created": [],
                "already_present": [],
                "superseded": [],
                "conflicts": [],
                "missing": [],
            },
            "error": {"type": type(error).__name__, "message": str(error)},
        }
        success = False
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
