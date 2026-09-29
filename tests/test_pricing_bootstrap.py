from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select

from configs.settings import settings
from core.model_runtime.factory import ModelFactory
from core.runtime_config import load_runtime_config
from scripts import bootstrap_database as bootstrap_module
from server.quota.bootstrap import (
    install_builtin_pricing_catalog,
    validate_pricing_coverage,
)
from server.quota.builtin_pricing import BUILTIN_PRICING_RULES
from server.quota.errors import QuotaDomainError, QuotaErrorCode
from server.quota.management import QuotaManagementService
from server.quota.models import PricingRuleModel


UTC = timezone.utc
ROLLOUT_AT = datetime(2026, 9, 5, 6, 0, tzinfo=UTC)


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    PricingRuleModel.__table__.create(engine)
    return engine


def _create_rule(
    service: QuotaManagementService,
    *,
    pricing_key: str,
    version: str,
    created_by: str,
    ordinary_input: int = 1,
):
    return service.create_pricing_rule(
        pricing_key=pricing_key,
        version=version,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        effective_until=None,
        ordinary_input_credits_micro_per_million_tokens=ordinary_input,
        cached_input_credits_micro_per_million_tokens=1,
        cache_write_credits_micro_per_million_tokens=1,
        output_credits_micro_per_million_tokens=1,
        reasoning_output_credits_micro_per_million_tokens=None,
        created_by=created_by,
    )


def test_managed_open_rule_is_closed_in_same_install_transaction() -> None:
    engine = _engine()
    service = QuotaManagementService(engine)
    definition = BUILTIN_PRICING_RULES[0]
    old = _create_rule(
        service,
        pricing_key=definition.pricing_key,
        version="legacy-v1",
        created_by="p4-model-pricing",
    )

    result = service.install_builtin_pricing_version(
        definition,
        installed_by="system:pricing-bootstrap",
        now=ROLLOUT_AT,
    )

    assert result["install_status"] == "superseded"
    assert result["superseded_rule_id"] == old["pricing_rule_id"]
    rows = service.list_pricing_rules(pricing_key=definition.pricing_key)
    current = next(row for row in rows if row["version"] == definition.version)
    predecessor = next(row for row in rows if row["version"] == "legacy-v1")
    assert predecessor["effective_until"] == current["effective_from"]
    assert predecessor["status"] == "retired"
    assert current["effective_from"] == ROLLOUT_AT.isoformat()
    service.close()
    engine.dispose()


def test_custom_overlapping_rule_is_reported_without_modification() -> None:
    engine = _engine()
    service = QuotaManagementService(engine)
    definition = BUILTIN_PRICING_RULES[0]
    custom = _create_rule(
        service,
        pricing_key=definition.pricing_key,
        version="custom-v1",
        created_by="developer-1",
    )

    with pytest.raises(QuotaDomainError) as raised:
        service.install_builtin_pricing_version(
            definition,
            installed_by="system:pricing-bootstrap",
            now=ROLLOUT_AT,
        )

    assert raised.value.code is QuotaErrorCode.PRICING_RULE_CONFLICT
    unchanged = service.get_pricing_rule(custom["pricing_rule_id"])
    assert unchanged["effective_until"] is None
    assert unchanged["status"] == "active"
    assert len(service.list_pricing_rules(pricing_key=definition.pricing_key)) == 1
    service.close()
    engine.dispose()


def test_same_builtin_version_with_different_rate_is_a_conflict() -> None:
    engine = _engine()
    service = QuotaManagementService(engine)
    definition = BUILTIN_PRICING_RULES[0]
    _create_rule(
        service,
        pricing_key=definition.pricing_key,
        version=definition.version,
        created_by="system:pricing-bootstrap",
        ordinary_input=definition.ordinary_input_credits_micro_per_million_tokens + 1,
    )

    with pytest.raises(QuotaDomainError) as raised:
        service.install_builtin_pricing_version(
            definition,
            installed_by="system:pricing-bootstrap",
            now=ROLLOUT_AT,
        )

    assert raised.value.code is QuotaErrorCode.PRICING_RULE_CONFLICT
    assert "ordinary_input" in str(raised.value)
    service.close()
    engine.dispose()


def test_builtin_catalog_covers_current_runtime_and_vision_rates() -> None:
    engine = _engine()
    install = install_builtin_pricing_catalog(engine, now=ROLLOUT_AT)
    config = ModelFactory.from_settings().config

    issues = validate_pricing_coverage(
        engine,
        config=config,
        runtime_config=load_runtime_config(),
        at=ROLLOUT_AT,
    )

    assert not [row for row in install["rules"] if row["status"] == "conflict"]
    assert issues == []
    with engine.connect() as connection:
        vision = connection.execute(
            select(PricingRuleModel).where(
                PricingRuleModel.pricing_key == "qwen/qwen3-vl-plus"
            )
        ).mappings().one()
    assert vision["visual_input_credits_micro_per_million_tokens"] is not None
    assert vision["image_unit_credits_micro"] is not None
    engine.dispose()


def test_catalog_reports_one_conflict_without_hiding_other_keys() -> None:
    engine = _engine()
    service = QuotaManagementService(engine)
    blocked_key = BUILTIN_PRICING_RULES[0].pricing_key
    custom = _create_rule(
        service,
        pricing_key=blocked_key,
        version="custom-v1",
        created_by="developer-1",
    )

    result = install_builtin_pricing_catalog(engine, now=ROLLOUT_AT)

    conflicts = [row for row in result["rules"] if row["status"] == "conflict"]
    created = [row for row in result["rules"] if row["status"] == "created"]
    assert [row["pricing_key"] for row in conflicts] == [blocked_key]
    assert custom["pricing_rule_id"] in conflicts[0]["message"]
    assert len(created) == len(BUILTIN_PRICING_RULES) - 1
    service.close()
    engine.dispose()


def test_concurrent_catalog_install_does_not_duplicate_versions(tmp_path) -> None:
    database_path = tmp_path / "pricing-bootstrap.sqlite3"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    engine = create_engine(database_url)
    PricingRuleModel.__table__.create(engine)
    engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _index: install_builtin_pricing_catalog(
                    database_url,
                    now=ROLLOUT_AT,
                ),
                range(2),
            )
        )

    assert not [
        row
        for result in results
        for row in result["rules"]
        if row["status"] == "conflict"
    ]
    engine = create_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(select(PricingRuleModel)).mappings().all()
    assert len(rows) == len(BUILTIN_PRICING_RULES)
    assert len({(row["pricing_key"], row["version"]) for row in rows}) == len(rows)
    engine.dispose()


@pytest.mark.parametrize(
    ("quota_enabled", "expected_success", "expected_warning"),
    [(True, False, False), (False, True, True)],
)
def test_bootstrap_enforces_or_warns_on_missing_coverage(
    monkeypatch,
    quota_enabled: bool,
    expected_success: bool,
    expected_warning: bool,
) -> None:
    monkeypatch.setattr(
        bootstrap_module,
        "install_builtin_pricing_catalog",
        lambda *args, **kwargs: {"rules": []},
    )
    monkeypatch.setattr(
        bootstrap_module,
        "validate_pricing_coverage",
        lambda *args, **kwargs: [
            {"pricing_key": "provider/missing", "reason": "missing"}
        ],
    )

    summary, success = bootstrap_module.bootstrap_database(
        database_url="sqlite+pysqlite:///:memory:",
        runtime_config=settings._config,
        quota_enforcement_enabled=quota_enabled,
        migrate=lambda url: None,
    )

    assert success is expected_success
    assert bool(summary["warnings"]) is expected_warning
    assert summary["pricing"]["missing"][0]["pricing_key"] == "provider/missing"
