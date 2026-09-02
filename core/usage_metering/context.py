"""Attribution and correlation helpers for capability usage metering."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.model_runtime.usage import (
    UsageAttributionContext,
    current_usage_attribution,
    resolve_usage_attribution,
)
from core.usage_metering.contracts import (
    CapabilityType,
    CapabilityUsageEvent,
    MeteredUsageItem,
    UsageSource,
    UsageStatus,
)


def create_capability_event(
    *,
    operation_id: str,
    capability_type: CapabilityType,
    provider: str,
    pricing_key: str,
    items: tuple[MeteredUsageItem, ...],
    usage_source: UsageSource,
    usage_status: UsageStatus,
    parent_operation_id: str | None = None,
    provider_response_id: str | None = None,
    raw_usage: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
    attribution: UsageAttributionContext | None = None,
) -> CapabilityUsageEvent:
    """Create a CapabilityUsageEvent bound to the ambient attribution context."""
    attr = attribution or current_usage_attribution()
    if attr is None:
        try:
            attr = resolve_usage_attribution()
        except Exception:
            attr = None

    request_id = attr.request_id if attr else "unknown-request"
    user_id = attr.user_id if attr else "unknown-user"
    workspace_id = attr.workspace_id if attr else None
    conversation_id = attr.conversation_id if attr else None
    turn_id = attr.turn_id if attr else None
    reservation_id = attr.reservation_id if attr else None
    worker_id = attr.worker_id if attr else None
    purpose = attr.purpose if attr else "other"
    resolved_parent_op = parent_operation_id or (attr.parent_operation_id if attr else None)

    at = occurred_at or datetime.now(timezone.utc)
    if at.tzinfo is None or at.utcoffset() is None:
        at = at.replace(tzinfo=timezone.utc)
    else:
        at = at.astimezone(timezone.utc)

    return CapabilityUsageEvent(
        operation_id=operation_id,
        parent_operation_id=resolved_parent_op,
        reservation_id=reservation_id,
        request_id=request_id,
        user_id=user_id,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        worker_id=worker_id,
        purpose=purpose,
        capability_type=capability_type,
        provider=provider,
        pricing_key=pricing_key,
        provider_response_id=provider_response_id,
        usage_source=usage_source,
        usage_status=usage_status,
        items=items,
        occurred_at=at,
        raw_usage=raw_usage or {},
    )
