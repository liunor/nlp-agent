from sqlalchemy import create_engine, select

from scripts.seed_extended_model_pricing import (
    CREDITS_MICRO_PER_CNY,
    PRICING_VERSION,
    UNVERIFIED_PRICING_KEYS,
    seed_verified_extended_model_pricing,
)
from server.quota.models import PricingRuleModel


def test_verified_extended_model_prices_are_versioned_and_idempotent() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    PricingRuleModel.__table__.create(engine)

    first = seed_verified_extended_model_pricing(
        engine, created_by="p4-model-pricing"
    )
    second = seed_verified_extended_model_pricing(
        engine, created_by="p4-model-pricing"
    )

    assert CREDITS_MICRO_PER_CNY == 1_000_000
    assert {item["status"] for item in first["rules"]} == {"created"}
    assert {item["status"] for item in second["rules"]} == {"already_present"}
    assert len(first["rules"]) == 10
    assert len(second["rules"]) == 10
    assert first["unverified_pricing_keys"] == []
    assert UNVERIFIED_PRICING_KEYS == ()

    with engine.connect() as connection:
        rows = {
            row["pricing_key"]: row
            for row in connection.execute(select(PricingRuleModel)).mappings()
        }
    assert set(rows) == {
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-pro",
        "qwen/qwen3.8-max",
        "qwen/qwen3.7-plus",
        "qwen/qwen3-vl-plus",
        "kimi/kimi-k2.6",
        "glm/glm-5.3",
        "glm/glm-5.2",
        "feature/image-understanding",
        "feature/link-read",
    }
    assert rows["qwen/qwen3-vl-plus"]["version"] == PRICING_VERSION
    assert rows["qwen/qwen3-vl-plus"][
        "ordinary_input_credits_micro_per_million_tokens"
    ] == 1_000_000
    assert rows["qwen/qwen3-vl-plus"][
        "cached_input_credits_micro_per_million_tokens"
    ] == 200_000
    assert rows["qwen/qwen3-vl-plus"][
        "cache_write_credits_micro_per_million_tokens"
    ] == 1_250_000
    assert rows["qwen/qwen3-vl-plus"][
        "output_credits_micro_per_million_tokens"
    ] == 10_000_000
    assert rows["qwen/qwen3-vl-plus"][
        "visual_input_credits_micro_per_million_tokens"
    ] == 1_000_000
    assert rows["qwen/qwen3-vl-plus"]["image_unit_credits_micro"] == 2_048
    assert rows["kimi/kimi-k2.6"]["version"] == PRICING_VERSION
    assert rows["kimi/kimi-k2.6"][
        "ordinary_input_credits_micro_per_million_tokens"
    ] == 6_500_000
    assert rows["kimi/kimi-k2.6"][
        "cached_input_credits_micro_per_million_tokens"
    ] == 1_100_000
    assert rows["kimi/kimi-k2.6"][
        "output_credits_micro_per_million_tokens"
    ] == 27_000_000
    assert rows["glm/glm-5.2"][
        "ordinary_input_credits_micro_per_million_tokens"
    ] == 8_000_000
    assert rows["glm/glm-5.2"][
        "cached_input_credits_micro_per_million_tokens"
    ] == 2_000_000
    assert rows["glm/glm-5.2"][
        "output_credits_micro_per_million_tokens"
    ] == 28_000_000
    assert rows["glm/glm-5.3"][
        "ordinary_input_credits_micro_per_million_tokens"
    ] == 8_000_000
    assert rows["glm/glm-5.3"][
        "cached_input_credits_micro_per_million_tokens"
    ] == 2_000_000
    assert rows["glm/glm-5.3"][
        "output_credits_micro_per_million_tokens"
    ] == 28_000_000
    assert rows["kimi/kimi-k2.6"][
        "reasoning_output_credits_micro_per_million_tokens"
    ] is None
    assert rows["glm/glm-5.2"][
        "reasoning_output_credits_micro_per_million_tokens"
    ] is None
    assert rows["glm/glm-5.3"][
        "reasoning_output_credits_micro_per_million_tokens"
    ] is None
    assert rows["qwen/qwen3.7-plus"]["search_call_credits_micro"] == 3_000
    assert rows["feature/image-understanding"]["image_unit_credits_micro"] == 0
    assert rows["feature/link-read"]["link_page_credits_micro"] == 0

    engine.dispose()
