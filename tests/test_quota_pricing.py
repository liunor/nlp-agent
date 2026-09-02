"""Behavioral tests for Phase 0 multi-provider credits calculation."""

from datetime import datetime, timezone
import uuid

import pytest

from core.model_runtime.usage import (
    CanonicalTokenUsage,
    InvocationOutcome,
    ModelIdentity,
    ModelInvocation,
    UsageAttributionContext,
)
from server.quota.pricing import (
    EstimatedUsageCannotBePricedError,
    PricingCatalog,
    PricingRule,
    UnknownUsageCannotBePricedError,
    UnknownPricingKeyError,
)


UTC = timezone.utc
AT_START = datetime(2026, 1, 1, tzinfo=UTC)


def _invocation(*, pricing_key: str | None = "deepseek/deepseek-v4-pro") -> ModelInvocation:
    return ModelInvocation(
        operation_id=str(uuid.uuid4()),
        identity=ModelIdentity(
            provider="deepseek",
            provider_model="deepseek-v4-pro",
            model_profile="teaching-pro",
            preset="coordinator-pro",
            route="coordinator",
            pricing_key=pricing_key,
            context_window_tokens=1_000_000,
            max_output_tokens=32_000,
        ),
        attribution=UsageAttributionContext(
            request_id="request-1",
            user_id="user-1",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            turn_id="turn-1",
            purpose="coordinator",
        ),
        attempt=1,
        fallback_index=0,
        started_at=AT_START,
    )


def _outcome(*, completed_at: datetime = AT_START) -> InvocationOutcome:
    return InvocationOutcome(status="succeeded", completed_at=completed_at)


def _rule(**changes: object) -> PricingRule:
    values: dict[str, object] = {
        "pricing_key": "deepseek/deepseek-v4-pro",
        "version": "2026-01",
        "effective_from": AT_START,
        "ordinary_input_credits_micro_per_million_tokens": 1_000_000,
        "cached_input_credits_micro_per_million_tokens": 250_000,
        "cache_write_credits_micro_per_million_tokens": 500_000,
        "output_credits_micro_per_million_tokens": 2_000_000,
        "reasoning_output_credits_micro_per_million_tokens": 3_000_000,
    }
    values.update(changes)
    return PricingRule(**values)


def test_prices_each_canonical_token_partition_once():
    """Would fail if cache or reasoning subsets were double charged."""
    usage = CanonicalTokenUsage(
        input_tokens=1_000_000,
        cached_input_tokens=200_000,
        cache_write_input_tokens=100_000,
        output_tokens=500_000,
        reasoning_output_tokens=100_000,
        total_tokens=1_500_000,
        source="provider",
    )

    priced = PricingCatalog([_rule()]).price(_invocation(), usage, _outcome())

    assert priced.ordinary_input_tokens == 700_000
    assert priced.cached_input_tokens == 200_000
    assert priced.cache_write_input_tokens == 100_000
    assert priced.ordinary_output_tokens == 400_000
    assert priced.reasoning_output_tokens == 100_000
    assert priced.credits_micro == 1_900_000


def test_rounds_the_full_microcredit_numerator_up_once():
    """Would fail if the million-token divisor were outside the ceiling operation."""
    usage = CanonicalTokenUsage(
        input_tokens=1,
        output_tokens=0,
        total_tokens=1,
        source="provider",
    )
    rule = _rule(
        ordinary_input_credits_micro_per_million_tokens=1,
        cached_input_credits_micro_per_million_tokens=0,
        cache_write_credits_micro_per_million_tokens=0,
        output_credits_micro_per_million_tokens=0,
        reasoning_output_credits_micro_per_million_tokens=None,
    )

    priced = PricingCatalog([rule]).price(_invocation(), usage, _outcome())

    assert priced.credits_micro == 1


def test_reasoning_is_included_in_output_when_no_separate_rate_exists():
    """Would fail if reasoning tokens were added on top of output pricing."""
    usage = CanonicalTokenUsage(
        input_tokens=0,
        output_tokens=100,
        reasoning_output_tokens=40,
        total_tokens=100,
        source="provider",
    )
    rule = _rule(
        ordinary_input_credits_micro_per_million_tokens=0,
        cached_input_credits_micro_per_million_tokens=0,
        cache_write_credits_micro_per_million_tokens=0,
        output_credits_micro_per_million_tokens=1_000_000,
        reasoning_output_credits_micro_per_million_tokens=None,
    )

    priced = PricingCatalog([rule]).price(_invocation(), usage, _outcome())

    assert priced.ordinary_output_tokens == 100
    assert priced.reasoning_output_tokens == 0
    assert priced.credits_micro == 100


def test_rejects_unknown_usage_instead_of_pricing_it_as_exact_zero():
    """Would fail if source=none silently became a free provider request."""
    usage = CanonicalTokenUsage(source="none")

    with pytest.raises(UnknownUsageCannotBePricedError):
        PricingCatalog([_rule()]).price(_invocation(), usage, _outcome())


def test_estimated_usage_requires_an_explicit_shadow_pricing_flag():
    """Would fail if rough context estimation became authoritative billing."""
    usage = CanonicalTokenUsage(
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        source="estimated",
    )
    catalog = PricingCatalog([_rule()])

    with pytest.raises(EstimatedUsageCannotBePricedError):
        catalog.price(_invocation(), usage, _outcome())

    priced = catalog.price(_invocation(), usage, _outcome(), allow_estimated=True)
    assert priced.usage_source == "estimated"


def test_selects_the_rule_by_pricing_key_not_display_profile():
    """Would fail if pricing looked up model_profile instead of pricing_key."""
    usage = CanonicalTokenUsage(
        input_tokens=1_000_000,
        output_tokens=0,
        total_tokens=1_000_000,
        source="provider",
    )

    with pytest.raises(UnknownPricingKeyError):
        PricingCatalog([_rule()]).price(
            _invocation(pricing_key="qwen/qwen3.8-max"), usage, _outcome()
        )


def test_selects_the_rule_active_when_the_attempt_completed():
    """Would fail if a historical UsageEvent used the current rule version."""
    old_rule = _rule(version="2026-01", effective_until=datetime(2026, 2, 1, tzinfo=UTC))
    new_rule = _rule(
        version="2026-02",
        effective_from=datetime(2026, 2, 1, tzinfo=UTC),
        ordinary_input_credits_micro_per_million_tokens=2_000_000,
    )
    usage = CanonicalTokenUsage(
        input_tokens=1_000_000,
        output_tokens=0,
        total_tokens=1_000_000,
        source="provider",
    )

    priced = PricingCatalog([old_rule, new_rule]).price(
        _invocation(), usage, _outcome(completed_at=datetime(2026, 2, 1, tzinfo=UTC))
    )

    assert priced.pricing_version == "2026-02"
    assert priced.credits_micro == 2_000_000


def test_rejects_overlapping_price_versions_for_one_pricing_key():
    """Would fail if a historical charge could choose between two prices."""
    with pytest.raises(ValueError, match="Overlapping pricing rules"):
        PricingCatalog([
            _rule(version="2026-01", effective_until=datetime(2026, 2, 15, tzinfo=UTC)),
            _rule(version="2026-02", effective_from=datetime(2026, 2, 1, tzinfo=UTC)),
        ])
