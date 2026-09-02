"""Reporters and holder slot for non-model capability usage."""

from __future__ import annotations

from typing import Protocol

from core.usage_metering.contracts import CapabilityUsageEvent


class CapabilityUsageConflictError(RuntimeError):
    """Raised when an operation_id is reported with conflicting facts."""


class CapabilityUsageReporter(Protocol):
    async def report(self, event: CapabilityUsageEvent) -> None:
        """Persist one capability execution idempotently by operation_id."""
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
