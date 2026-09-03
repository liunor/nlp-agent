"""Exact, versioned Meter Pricing calculation over capability usage items."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable

from core.usage_metering.contracts import (
    CapabilityUsageEvent,
    MeteredUsageItem,
    MeterPricingRule,
    PricedCapabilityUsage,
    PricedMeterUsageItem,
)
from server.quota.pricing import PricingError


class MeterPricingError(PricingError):
    """Base error for capability meter pricing failures."""


class UnknownMeterPricingError(MeterPricingError):
    """Raised when a pricing key or meter has no effective rule at the event time."""


class MeterPricingRuleConflictError(MeterPricingError):
    """Raised when active meter pricing rules have overlapping effective ranges."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class MeterPricingCatalog:
    """Selects active rules and calculates μcredits for CapabilityUsageEvent items."""

    def __init__(self, rules: Iterable[MeterPricingRule]) -> None:
        grouped: dict[tuple[str, str], list[MeterPricingRule]] = defaultdict(list)
        for rule in rules:
            grouped[(rule.pricing_key, rule.meter)].append(rule)
        self._rules_by_key_meter = {
            key: tuple(sorted(items, key=lambda r: r.effective_from))
            for key, items in grouped.items()
        }
        self._validate_ranges()

    def _validate_ranges(self) -> None:
        for (key, meter), rules in self._rules_by_key_meter.items():
            usable_rules = [r for r in rules if r.status in {"active", "retired"}]
            for i in range(len(usable_rules) - 1):
                first = usable_rules[i]
                second = usable_rules[i + 1]
                if first.effective_until is None or first.effective_until > second.effective_from:
                    raise MeterPricingRuleConflictError(
                        f"Overlapping meter pricing rules for {key} / {meter}: "
                        f"version {first.version} ({first.effective_from} to {first.effective_until}) "
                        f"overlaps with {second.version} ({second.effective_from})"
                    )

    def select_rule(
        self,
        pricing_key: str,
        meter: str,
        at: datetime,
    ) -> MeterPricingRule:
        time_utc = _utc(at)
        rules = self._rules_by_key_meter.get((pricing_key, meter), ())
        for rule in rules:
            if rule.status not in {"active", "retired"}:
                continue
            if rule.effective_from <= time_utc and (
                rule.effective_until is None or time_utc < rule.effective_until
            ):
                return rule
        raise UnknownMeterPricingError(
            f"No active meter pricing rule or retired historical rule for "
            f"pricing_key={pricing_key!r}, "
            f"meter={meter!r} at {time_utc.isoformat()}"
        )

    def price_event(self, event: CapabilityUsageEvent) -> PricedCapabilityUsage:
        if event.usage_status in {"pending", "unavailable"}:
            priced_items: list[PricedMeterUsageItem] = []
            pricing_version = "pending"
            for item in event.items:
                try:
                    rule = self.select_rule(event.pricing_key, item.meter, event.occurred_at)
                    self._validate_unit(item, rule)
                    rate_unit = rule.rate_unit
                    rate_micro = rule.rate_micro
                    pricing_version = rule.version
                except UnknownMeterPricingError:
                    rate_unit = 1
                    rate_micro = 0
                priced_items.append(
                    PricedMeterUsageItem(
                        meter=item.meter,
                        quantity=item.quantity,
                        unit=item.unit,
                        rate_unit=rate_unit,
                        rate_micro=rate_micro,
                        line_credits_micro=0,
                    )
                )
            return PricedCapabilityUsage(
                operation_id=event.operation_id,
                pricing_key=event.pricing_key,
                pricing_version=pricing_version,
                usage_source=event.usage_source,
                usage_status=event.usage_status,
                credits_micro=0,
                items=tuple(priced_items),
            )

        priced_items = []
        max_minimum_charge = 0
        rule_version = ""
        for item in event.items:
            rule = self.select_rule(event.pricing_key, item.meter, event.occurred_at)
            self._validate_unit(item, rule)
            rule_version = rule.version
            max_minimum_charge = max(max_minimum_charge, rule.minimum_charge_micro)

            # Integer ceiling arithmetic: ceil(quantity * rate_micro / rate_unit)
            if item.quantity == 0 or rule.rate_micro == 0:
                line_micro = 0
            else:
                line_micro = (
                    item.quantity * rule.rate_micro + rule.rate_unit - 1
                ) // rule.rate_unit

            priced_items.append(
                PricedMeterUsageItem(
                    meter=item.meter,
                    quantity=item.quantity,
                    unit=item.unit,
                    rate_unit=rule.rate_unit,
                    rate_micro=rule.rate_micro,
                    line_credits_micro=line_micro,
                )
            )

        sum_micro = sum(item.line_credits_micro for item in priced_items)
        total_micro = max(max_minimum_charge, sum_micro)

        return PricedCapabilityUsage(
            operation_id=event.operation_id,
            pricing_key=event.pricing_key,
            pricing_version=rule_version or "default",
            usage_source=event.usage_source,
            usage_status=event.usage_status,
            credits_micro=total_micro,
            items=tuple(priced_items),
        )

    def estimate_micro(
        self,
        *,
        pricing_key: str,
        items: Iterable[MeteredUsageItem],
        at: datetime,
    ) -> int:
        """Price a conservative pre-execution meter estimate with production rules."""
        total = 0
        minimum = 0
        for item in items:
            rule = self.select_rule(pricing_key, item.meter, at)
            self._validate_unit(item, rule)
            minimum = max(minimum, rule.minimum_charge_micro)
            total += (
                item.quantity * rule.rate_micro + rule.rate_unit - 1
            ) // rule.rate_unit
        return max(minimum, total)

    @staticmethod
    def _validate_unit(item: MeteredUsageItem, rule: MeterPricingRule) -> None:
        if item.unit != rule.unit:
            raise MeterPricingError(
                f"Unit mismatch for {item.meter!r}: {item.unit!r} != {rule.unit!r}"
            )
