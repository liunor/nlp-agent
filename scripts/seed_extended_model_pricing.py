"""Preview or manually install the application's built-in pricing catalog."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from dotenv import dotenv_values

from configs.settings import BASE_DIR
from server.quota.bootstrap import install_builtin_pricing_catalog
from server.quota.builtin_pricing import (
    BUILTIN_PRICING_RULES,
    CREDITS_MICRO_PER_CNY,
    EFFECTIVE_FROM,
    PRICING_VERSION,
    UNVERIFIED_PRICING_KEYS,
)


VERIFIED_RULES: tuple[dict[str, Any], ...] = tuple(
    rule.model_dump(mode="json") for rule in BUILTIN_PRICING_RULES
)


def seed_verified_extended_model_pricing(
    database: str | Any,
    *,
    created_by: str,
) -> dict[str, Any]:
    """Compatibility wrapper around the deployment pricing installer."""
    return install_builtin_pricing_catalog(database, installed_by=created_by)


def _preview() -> dict[str, Any]:
    return {
        "currency": "CNY",
        "credits_micro_per_cny": CREDITS_MICRO_PER_CNY,
        "version": PRICING_VERSION,
        "effective_from": EFFECTIVE_FROM.isoformat(),
        "verified_rules": list(VERIFIED_RULES),
        "unverified_pricing_keys": list(UNVERIFIED_PRICING_KEYS),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview or install Nova's evidence-backed pricing catalog."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the rules; without this flag, only print a preview.",
    )
    parser.add_argument("--created-by", default="p4-model-pricing")
    args = parser.parse_args()
    if not args.apply:
        print(json.dumps(_preview(), ensure_ascii=False, indent=2))
        return

    database_url = str(
        os.environ.get("NLP_AGENT_DATABASE_URL")
        or dotenv_values(BASE_DIR / ".env").get("NLP_AGENT_DATABASE_URL")
        or ""
    ).strip()
    if not database_url:
        raise SystemExit("NLP_AGENT_DATABASE_URL is not configured")
    result = seed_verified_extended_model_pricing(
        database_url,
        created_by=args.created_by,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if any(item["status"] == "conflict" for item in result["rules"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
