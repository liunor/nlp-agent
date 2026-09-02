"""Exact, versioned Credits calculation over runtime canonical usage."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Annotated, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.model_runtime.usage import (
    CanonicalTokenUsage,
    InvocationOutcome,
    ModelInvocation,
)


TOKENS_PER_MILLION = 1_000_000
StrictNonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
StrictPositiveInt = Annotated[int, Field(strict=True, ge=1)]
UsageSource = Literal["provider", "estimated", "none"]


class PricingError(RuntimeError):
    """Base error for deterministic pricing failures."""


class UnknownPricingKeyError(PricingError):
    """Raised when runtime identity has no active matching price rule."""


class UnknownUsageCannotBePricedError(PricingError):
    """Raised instead of incorrectly treating missing Provider usage as free."""


class EstimatedUsageCannotBePricedError(PricingError):
    """Raised unless a caller explicitly permits estimated shadow pricing."""


class PricingFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PricingRule(PricingFrozenModel):
    """One immutable Credits price version for one runtime pricing key."""

    pricing_key: str = Field(min_length=1)
    version: str = Field(min_length=1)
    effective_from: datetime
    effective_until: datetime | None = None
    ordinary_input_credits_micro_per_million_tokens: StrictNonNegativeInt
    cached_input_credits_micro_per_million_tokens: StrictNonNegativeInt
    cache_write_credits_micro_per_million_tokens: StrictNonNegativeInt
    output_credits_micro_per_million_tokens: StrictNonNegativeInt
    reasoning_output_credits_micro_per_million_tokens: StrictNonNegativeInt | None = None

    @field_validator("effective_from", "effective_until")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("pricing rule effective time must be timezone-aware")
        if value.utcoffset().total_seconds() != 0:
            raise ValueError("pricing rule effective time must use UTC")
        return value

    @model_validator(mode="after")
    def validate_effective_range(self) -> "PricingRule":
        if self.effective_until is not None and self.effective_until <= self.effective_from:
            raise ValueError("effective_until must be after effective_from")
        return self


class PricedUsage(PricingFrozenModel):
    """The reproducible result of pricing one canonical Provider attempt."""

    pricing_key: str = Field(min_length=1)
    pricing_version: str = Field(min_length=1)
    usage_source: UsageSource
    ordinary_input_tokens: StrictNonNegativeInt
    cached_input_tokens: StrictNonNegativeInt
    cache_write_input_tokens: StrictNonNegativeInt
    ordinary_output_tokens: StrictNonNegativeInt
    reasoning_output_tokens: StrictNonNegativeInt
    credits_micro: StrictNonNegativeInt


class PricingCatalog:
    """Selects one active rule and prices all canonical Token partitions once."""

    def __init__(self, rules: Iterable[PricingRule]) -> None:
        grouped: dict[str, list[PricingRule]] = defaultdict(list)
        for rule in rules:
            grouped[rule.pricing_key].append(rule)
        self._rules_by_key = {
            pricing_key: tuple(sorted(key_rules, key=lambda item: item.effective_from))
            for pricing_key, key_rules in grouped.items()
        }
        self._validate_ranges()

    def _validate_ranges(self) -> None:
        for pricing_key, rules in self._rules_by_key.items():
            for current, following in zip(rules, rules[1:]):
                if current.effective_until is None or following.effective_from < current.effective_until:
                    raise ValueError(
                        f"Overlapping pricing rules for pricing_key {pricing_key!r}"
                    )

    def price(
        self,
        invocation: ModelInvocation,
        usage: CanonicalTokenUsage,
        outcome: InvocationOutcome,
        *,
        allow_estimated: bool = False,
    ) -> PricedUsage:
        if usage.source == "none":
            raise UnknownUsageCannotBePricedError(
                "Canonical usage source=none must enter reconciliation or a conservative policy"
            )
        if usage.source == "estimated" and not allow_estimated:
            raise EstimatedUsageCannotBePricedError(
                "Estimated usage can only be priced by an explicit shadow or conservative policy"
            )

        pricing_key = invocation.identity.pricing_key
        rule = self._active_rule(pricing_key, outcome.completed_at)
        ordinary_input = (
            usage.input_tokens
            - usage.cached_input_tokens
            - usage.cache_write_input_tokens
        )
        if rule.reasoning_output_credits_micro_per_million_tokens is None:
            ordinary_output = usage.output_tokens
            reasoning_output = 0
        else:
            ordinary_output = usage.output_tokens - usage.reasoning_output_tokens
            reasoning_output = usage.reasoning_output_tokens

        numerator = (
            ordinary_input * rule.ordinary_input_credits_micro_per_million_tokens
            + usage.cached_input_tokens
            * rule.cached_input_credits_micro_per_million_tokens
            + usage.cache_write_input_tokens
            * rule.cache_write_credits_micro_per_million_tokens
            + ordinary_output * rule.output_credits_micro_per_million_tokens
            + reasoning_output
            * (rule.reasoning_output_credits_micro_per_million_tokens or 0)
        )
        credits_micro = self._ceil_divide(numerator, TOKENS_PER_MILLION)

        return PricedUsage(
            pricing_key=rule.pricing_key,
            pricing_version=rule.version,
            usage_source=usage.source,
            ordinary_input_tokens=ordinary_input,
            cached_input_tokens=usage.cached_input_tokens,
            cache_write_input_tokens=usage.cache_write_input_tokens,
            ordinary_output_tokens=ordinary_output,
            reasoning_output_tokens=reasoning_output,
            credits_micro=credits_micro,
        )

    def _active_rule(
        self, pricing_key: str | None, completed_at: datetime
    ) -> PricingRule:
        if pricing_key is None:
            raise UnknownPricingKeyError("Runtime ModelIdentity has no pricing_key")
        matches = [
            rule
            for rule in self._rules_by_key.get(pricing_key, ())
            if rule.effective_from <= completed_at
            and (rule.effective_until is None or completed_at < rule.effective_until)
        ]
        if len(matches) != 1:
            raise UnknownPricingKeyError(
                f"Expected exactly one active pricing rule for {pricing_key!r} at {completed_at.isoformat()}"
            )
        return matches[0]

    @staticmethod
    def _ceil_divide(numerator: int, denominator: StrictPositiveInt) -> int:
        return (numerator + denominator - 1) // denominator
