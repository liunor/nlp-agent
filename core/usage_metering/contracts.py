"""Public contracts for non-model capability metering and versioned pricing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


StrictNonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
StrictPositiveInt = Annotated[int, Field(strict=True, ge=1)]
CapabilityType = Literal["search", "web_fetch", "ocr"]
UsageSource = Literal["provider", "measured", "estimated", "none"]
UsageStatus = Literal["exact", "estimated", "pending", "unavailable"]


def _require_utc(value: datetime | None, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field_name} must use UTC")
    return value


class MeteringFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MeteredUsageItem(MeteringFrozenModel):
    meter: str = Field(min_length=1, max_length=128)
    quantity: StrictNonNegativeInt
    unit: str = Field(min_length=1, max_length=32)


class CapabilityUsageEvent(MeteringFrozenModel):
    operation_id: str = Field(min_length=1, max_length=128)
    parent_operation_id: str | None = Field(default=None, max_length=128)
    reservation_id: str | None = Field(default=None, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    workspace_id: str | None = Field(default=None, max_length=128)
    conversation_id: str | None = Field(default=None, max_length=128)
    turn_id: str | None = Field(default=None, max_length=128)
    worker_id: str | None = Field(default=None, max_length=128)
    purpose: str = Field(min_length=1, max_length=32)
    capability_type: CapabilityType
    provider: str = Field(min_length=1, max_length=128)
    pricing_key: str = Field(min_length=1, max_length=255)
    provider_response_id: str | None = Field(default=None, max_length=255)
    usage_source: UsageSource
    usage_status: UsageStatus
    items: tuple[MeteredUsageItem, ...]
    occurred_at: datetime
    raw_usage: dict = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        return _require_utc(value, field_name="occurred_at")  # type: ignore[return-value]


class MeterPricingRule(MeteringFrozenModel):
    capability_type: str = Field(default="", max_length=64)
    pricing_key: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=64)
    meter: str = Field(min_length=1, max_length=128)
    unit: str = Field(min_length=1, max_length=32)
    rate_unit: StrictPositiveInt
    rate_micro: StrictNonNegativeInt
    minimum_charge_micro: StrictNonNegativeInt = 0
    effective_from: datetime
    effective_until: datetime | None = None
    status: str = "active"
    created_by: str = "system"
    created_at: datetime | None = None

    @property
    def min_charge_micro(self) -> int:
        return self.minimum_charge_micro

    @field_validator("effective_from", "effective_until", "created_at")
    @classmethod
    def validate_utc(cls, value: datetime | None, info) -> datetime | None:
        return _require_utc(value, field_name=info.field_name)

    @model_validator(mode="after")
    def validate_effective_range(self) -> "MeterPricingRule":
        if self.effective_until is not None and self.effective_until <= self.effective_from:
            raise ValueError("effective_until must be after effective_from")
        return self

    def overlaps(self, other: "MeterPricingRule") -> bool:
        if self.pricing_key != other.pricing_key or self.meter != other.meter:
            return False
        self_until = self.effective_until or datetime.max.replace(tzinfo=timezone.utc)
        other_until = other.effective_until or datetime.max.replace(tzinfo=timezone.utc)
        return max(self.effective_from, other.effective_from) < min(self_until, other_until)


class PricedMeterUsageItem(MeteringFrozenModel):
    meter: str = Field(min_length=1, max_length=128)
    quantity: StrictNonNegativeInt
    unit: str = Field(min_length=1, max_length=32)
    rate_unit: StrictPositiveInt
    rate_micro: StrictNonNegativeInt
    line_credits_micro: StrictNonNegativeInt


class PricedCapabilityUsage(MeteringFrozenModel):
    operation_id: str = Field(min_length=1, max_length=128)
    pricing_key: str = Field(min_length=1, max_length=255)
    pricing_version: str = Field(min_length=1, max_length=64)
    usage_source: UsageSource
    usage_status: UsageStatus
    credits_micro: StrictNonNegativeInt
    items: tuple[PricedMeterUsageItem, ...]
