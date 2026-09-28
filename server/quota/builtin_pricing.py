"""Evidence-backed pricing definitions shipped with the application.

This module is intentionally pure: it does not read environment variables and
does not connect to the database.  Deployment, the manual seed command, and
tests all consume this single catalog.
"""

from __future__ import annotations

from datetime import datetime, timezone

from server.quota.pricing import PricingRule


UTC = timezone.utc
CREDITS_MICRO_PER_CNY = 1_000_000
PRICING_VERSION = "official-cny-2026-09-05"
EFFECTIVE_FROM = datetime(2026, 9, 4, 16, 0, tzinfo=UTC)
BUILTIN_PRICING_CREATED_BY = "system:pricing-bootstrap"


def _rule(
    pricing_key: str,
    *,
    ordinary_input: int,
    cached_input: int,
    cache_write: int,
    output: int,
    reasoning_output: int | None = None,
    visual_input: int | None = None,
    image_unit: int | None = None,
    search_call: int | None = None,
    link_page: int | None = None,
) -> PricingRule:
    return PricingRule(
        pricing_key=pricing_key,
        version=PRICING_VERSION,
        effective_from=EFFECTIVE_FROM,
        ordinary_input_credits_micro_per_million_tokens=ordinary_input,
        cached_input_credits_micro_per_million_tokens=cached_input,
        cache_write_credits_micro_per_million_tokens=cache_write,
        output_credits_micro_per_million_tokens=output,
        reasoning_output_credits_micro_per_million_tokens=reasoning_output,
        visual_input_credits_micro_per_million_tokens=visual_input,
        image_unit_credits_micro=image_unit,
        search_call_credits_micro=search_call,
        link_page_credits_micro=link_page,
    )


# The static Credits catalog deliberately uses the published on-demand price,
# not temporary discounts.  DeepSeek uses the published peak price because the
# database schema cannot express recurring peak/off-peak windows.  Qwen 3.7
# Plus uses the thinking-mode input rate because one pricing_key is shared by
# thinking and non-thinking presets; this is conservative for admission and
# avoids under-accounting.  Cache fields fall back to ordinary input whenever
# the configured model does not report cache usage.
BUILTIN_PRICING_RULES: tuple[PricingRule, ...] = (
    _rule(
        "deepseek/deepseek-v4-flash",
        ordinary_input=3_000_000,
        cached_input=100_000,
        cache_write=3_000_000,
        output=9_000_000,
    ),
    _rule(
        "deepseek/deepseek-v4-pro",
        ordinary_input=9_000_000,
        cached_input=300_000,
        cache_write=9_000_000,
        output=27_000_000,
    ),
    _rule(
        "qwen/qwen3.8-max",
        ordinary_input=12_000_000,
        cached_input=12_000_000,
        cache_write=12_000_000,
        output=36_000_000,
    ),
    _rule(
        "qwen/qwen3.7-plus",
        ordinary_input=8_000_000,
        cached_input=8_000_000,
        cache_write=8_000_000,
        output=8_000_000,
        # Beijing turbo native search: CNY 3 / 1,000 calls.
        search_call=3_000,
    ),
    _rule(
        "qwen/qwen3-vl-plus",
        ordinary_input=1_000_000,
        cached_input=200_000,
        cache_write=1_250_000,
        output=10_000_000,
        visual_input=1_000_000,
        # Conservative admission hold; provider visual tokens replace it at
        # settlement when available.
        image_unit=2_048,
    ),
    _rule(
        "kimi/kimi-k2.6",
        ordinary_input=6_500_000,
        cached_input=1_100_000,
        cache_write=6_500_000,
        output=27_000_000,
    ),
    _rule(
        "glm/glm-5.3",
        ordinary_input=8_000_000,
        cached_input=2_000_000,
        cache_write=8_000_000,
        output=28_000_000,
    ),
    _rule(
        "glm/glm-5.2",
        ordinary_input=8_000_000,
        cached_input=2_000_000,
        cache_write=8_000_000,
        output=28_000_000,
    ),
    # RapidOCR and the guarded HTTP fetcher run inside Nova and currently have
    # no external per-call charge.  Explicit zero unit prices are product
    # policy, not placeholders for unknown provider prices.
    _rule(
        "feature/image-understanding",
        ordinary_input=0,
        cached_input=0,
        cache_write=0,
        output=0,
        image_unit=0,
    ),
    _rule(
        "feature/link-read",
        ordinary_input=0,
        cached_input=0,
        cache_write=0,
        output=0,
        link_page=0,
    ),
)

UNVERIFIED_PRICING_KEYS: tuple[str, ...] = ()
