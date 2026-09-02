"""Unit tests for MeterPricingCatalog integer calculations and range checking."""

from datetime import datetime, timezone
import pytest

from core.usage_metering.contracts import (
    CapabilityUsageEvent,
    MeterPricingRule,
    MeteredUsageItem,
)
from server.quota.meter_pricing import (
    MeterPricingCatalog,
    MeterPricingRuleConflictError,
    UnknownMeterPricingError,
)

UTC = timezone.utc


def test_meter_pricing_integer_ceil_calculations():
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
    rule = MeterPricingRule(
        pricing_key="qwen/cn-beijing/web-search/turbo",
        version="2026-09-01",
        meter="search.requests",
        unit="call",
        rate_unit=1000,
        rate_micro=3_000_000,  # 3000 microcredits per call
        minimum_charge_micro=0,
        effective_from=datetime(2026, 9, 1, tzinfo=UTC),
    )
    catalog = MeterPricingCatalog([rule])

    # 1 call: ceil(1 * 3000000 / 1000) = 3000
    event1 = CapabilityUsageEvent(
        operation_id="op-1",
        request_id="req-1",
        user_id="user-1",
        purpose="worker",
        capability_type="search",
        provider="qwen",
        pricing_key="qwen/cn-beijing/web-search/turbo",
        usage_source="provider",
        usage_status="exact",
        items=(MeteredUsageItem(meter="search.requests", quantity=1, unit="call"),),
        occurred_at=now,
    )
    priced1 = catalog.price_event(event1)
    assert priced1.credits_micro == 3000
    assert priced1.items[0].line_credits_micro == 3000
    assert priced1.pricing_version == "2026-09-01"

    # Fraction ceil test: 1 unit with rate 1 per 1000 -> ceil(1/1000) = 1
    small_rule = MeterPricingRule(
        pricing_key="internal/web-fetch/v1",
        version="2026-09-01",
        meter="web_fetch.bytes",
        unit="byte",
        rate_unit=1000,
        rate_micro=1,
        minimum_charge_micro=0,
        effective_from=datetime(2026, 9, 1, tzinfo=UTC),
    )
    small_catalog = MeterPricingCatalog([small_rule])
    event_byte = CapabilityUsageEvent(
        operation_id="op-byte",
        request_id="req-1",
        user_id="user-1",
        purpose="worker",
        capability_type="web_fetch",
        provider="internal",
        pricing_key="internal/web-fetch/v1",
        usage_source="measured",
        usage_status="exact",
        items=(MeteredUsageItem(meter="web_fetch.bytes", quantity=1, unit="byte"),),
        occurred_at=now,
    )
    priced_byte = small_catalog.price_event(event_byte)
    assert priced_byte.credits_micro == 1


def test_meter_pricing_zero_rate_and_zero_quantity():
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
    rule = MeterPricingRule(
        pricing_key="internal/rapidocr/v1",
        version="2026-09-01",
        meter="ocr.pages",
        unit="page",
        rate_unit=1,
        rate_micro=0,
        minimum_charge_micro=0,
        effective_from=datetime(2026, 9, 1, tzinfo=UTC),
    )
    catalog = MeterPricingCatalog([rule])

    event = CapabilityUsageEvent(
        operation_id="op-ocr",
        request_id="req-1",
        user_id="user-1",
        purpose="vision",
        capability_type="ocr",
        provider="internal",
        pricing_key="internal/rapidocr/v1",
        usage_source="measured",
        usage_status="exact",
        items=(MeteredUsageItem(meter="ocr.pages", quantity=5, unit="page"),),
        occurred_at=now,
    )
    priced = catalog.price_event(event)
    assert priced.credits_micro == 0
    assert priced.items[0].line_credits_micro == 0


def test_meter_pricing_minimum_charge_enforcement():
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
    rule = MeterPricingRule(
        pricing_key="provider/expensive-tool",
        version="v1",
        meter="tool.calls",
        unit="call",
        rate_unit=1,
        rate_micro=100,
        minimum_charge_micro=500,
        effective_from=datetime(2026, 9, 1, tzinfo=UTC),
    )
    catalog = MeterPricingCatalog([rule])

    event = CapabilityUsageEvent(
        operation_id="op-min",
        request_id="req-1",
        user_id="user-1",
        purpose="worker",
        capability_type="search",
        provider="provider",
        pricing_key="provider/expensive-tool",
        usage_source="provider",
        usage_status="exact",
        items=(MeteredUsageItem(meter="tool.calls", quantity=1, unit="call"),),
        occurred_at=now,
    )
    priced = catalog.price_event(event)
    assert priced.items[0].line_credits_micro == 100
    assert priced.credits_micro == 500  # Enforced minimum charge


def test_meter_pricing_rejects_overlapping_ranges():
    rule1 = MeterPricingRule(
        pricing_key="qwen/cn-beijing/web-search/turbo",
        version="v1",
        meter="search.requests",
        unit="call",
        rate_unit=1,
        rate_micro=10,
        effective_from=datetime(2026, 9, 1, tzinfo=UTC),
        effective_until=datetime(2026, 9, 10, tzinfo=UTC),
    )
    rule2 = MeterPricingRule(
        pricing_key="qwen/cn-beijing/web-search/turbo",
        version="v2",
        meter="search.requests",
        unit="call",
        rate_unit=1,
        rate_micro=20,
        effective_from=datetime(2026, 9, 5, tzinfo=UTC),  # Overlaps with rule1
        effective_until=None,
    )
    with pytest.raises(MeterPricingRuleConflictError, match="Overlapping meter pricing rules"):
        MeterPricingCatalog([rule1, rule2])


def test_meter_pricing_missing_rule_raises_error():
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
    catalog = MeterPricingCatalog([])
    event = CapabilityUsageEvent(
        operation_id="op-missing",
        request_id="req-1",
        user_id="user-1",
        purpose="worker",
        capability_type="search",
        provider="qwen",
        pricing_key="unknown/pricing/key",
        usage_source="provider",
        usage_status="exact",
        items=(MeteredUsageItem(meter="search.requests", quantity=1, unit="call"),),
        occurred_at=now,
    )
    with pytest.raises(UnknownMeterPricingError, match="No active meter pricing rule"):
        catalog.price_event(event)


def test_meter_pricing_pending_and_unavailable_status():
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
    rule = MeterPricingRule(
        pricing_key="qwen/cn-beijing/web-search/turbo",
        version="2026-09-01",
        meter="search.requests",
        unit="call",
        rate_unit=1,
        rate_micro=1000,
        effective_from=datetime(2026, 9, 1, tzinfo=UTC),
    )
    catalog = MeterPricingCatalog([rule])
    event_pending = CapabilityUsageEvent(
        operation_id="op-pending",
        request_id="req-1",
        user_id="user-1",
        purpose="worker",
        capability_type="search",
        provider="qwen",
        pricing_key="qwen/cn-beijing/web-search/turbo",
        usage_source="provider",
        usage_status="pending",
        items=(MeteredUsageItem(meter="search.requests", quantity=1, unit="call"),),
        occurred_at=now,
    )
    priced = catalog.price_event(event_pending)
    assert priced.credits_micro == 0
    assert priced.usage_status == "pending"
