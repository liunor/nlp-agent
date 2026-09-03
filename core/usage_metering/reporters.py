"""Reporters and holder slot for non-model capability usage."""

from __future__ import annotations

from typing import Any, Protocol

from core.usage_metering.contracts import CapabilityUsageEvent, MeteredUsageItem


class CapabilityUsageConflictError(RuntimeError):
    """Raised when an operation_id is reported with conflicting facts."""


class CapabilityUsageReporter(Protocol):
    async def report(self, event: CapabilityUsageEvent) -> None:
        """Persist one capability execution idempotently by operation_id."""
        ...

    async def reserve_additional(
        self,
        *,
        reservation_id: str,
        operation_key: str,
        estimated_micro: int,
        reason: str,
        pricing_key: str | None = None,
        estimated_items: tuple[MeteredUsageItem, ...] = (),
    ) -> Any:
        """Dynamically reserve additional quota before capability execution."""
        ...


class CapabilityUsageReporterSlot:
    """A mutable holder for the active CapabilityUsageReporter instance."""

    def __init__(
        self,
        reporter: CapabilityUsageReporter | None = None,
        *,
        required: bool = False,
    ) -> None:
        self.reporter = reporter
        self.required = required

    def configure(
        self, reporter: CapabilityUsageReporter | None, *, required: bool = False
    ) -> None:
        self.reporter = reporter
        self.required = required


_GLOBAL_CAPABILITY_REPORTER_SLOT = CapabilityUsageReporterSlot()


def get_global_capability_reporter_slot() -> CapabilityUsageReporterSlot:
    return _GLOBAL_CAPABILITY_REPORTER_SLOT


def configure_global_capability_usage_reporter(
    reporter: CapabilityUsageReporter | None,
    *,
    required: bool = False,
) -> None:
    _GLOBAL_CAPABILITY_REPORTER_SLOT.configure(reporter, required=required)


class InMemoryCapabilityUsageReporter:
    """In-memory implementation for tests."""

    def __init__(self) -> None:
        self.events: list[CapabilityUsageEvent] = []
        self.reservations: list[dict[str, object]] = []
        self._by_op: dict[str, CapabilityUsageEvent] = {}

    async def report(self, event: CapabilityUsageEvent) -> None:
        existing = self._by_op.get(event.operation_id)
        if existing is not None:
            if existing == event:
                return
            raise CapabilityUsageConflictError(
                f"Conflicting capability usage report for {event.operation_id}"
            )
        self._by_op[event.operation_id] = event
        self.events.append(event)

    async def reserve_additional(
        self,
        *,
        reservation_id: str,
        operation_key: str,
        estimated_micro: int,
        reason: str,
        pricing_key: str | None = None,
        estimated_items: tuple[MeteredUsageItem, ...] = (),
    ) -> Any:
        self.reservations.append(
            {
                "reservation_id": reservation_id,
                "operation_key": operation_key,
                "estimated_micro": estimated_micro,
                "reason": reason,
                "pricing_key": pricing_key,
                "estimated_items": estimated_items,
            }
        )
        return None


async def pre_reserve_capability(
    *,
    operation_key: str,
    estimated_micro: int | None = None,
    reason: str,
    pricing_key: str | None = None,
    estimated_items: tuple[MeteredUsageItem, ...] = (),
    reservation_id: str | None = None,
) -> None:
    """Check and dynamically reserve additional quota before capability execution."""
    from core.model_runtime.usage import current_usage_attribution

    attribution = current_usage_attribution()
    resolved_reservation_id = reservation_id or (
        attribution.reservation_id if attribution is not None else None
    )
    if resolved_reservation_id is None:
        return
    slot = get_global_capability_reporter_slot()
    if slot.reporter is None:
        if slot.required:
            raise CapabilityUsageConflictError(
                "This capability process requires a configured capability usage Reporter"
            )
        return
    reserve_func = getattr(slot.reporter, "reserve_additional", None)
    if reserve_func is None:
        if slot.required:
            raise CapabilityUsageConflictError(
                "The configured capability usage Reporter cannot reserve quota"
            )
        return
    await reserve_func(
        reservation_id=resolved_reservation_id,
        operation_key=operation_key,
        estimated_micro=estimated_micro,
        reason=reason,
        pricing_key=pricing_key,
        estimated_items=estimated_items,
    )
