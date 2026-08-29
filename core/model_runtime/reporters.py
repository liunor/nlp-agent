"""In-memory and slot-based ModelUsageReporter implementations for tests and lifecycle wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.model_runtime.usage import (
    CanonicalTokenUsage,
    InvocationOutcome,
    ModelInvocation,
    ModelUsageReporter,
)

if TYPE_CHECKING:
    pass


class UsageEventConflictError(RuntimeError):
    """Raised when an operation_id is reported with conflicting data."""


class ModelUsageReporterSlot:
    """A mutable holder for the active ModelUsageReporter instance."""

    def __init__(self, reporter: ModelUsageReporter | None = None) -> None:
        self.reporter = reporter

    def configure(self, reporter: ModelUsageReporter | None) -> None:
        self.reporter = reporter


def configure_global_model_usage_reporter(reporter: ModelUsageReporter | None) -> None:
    """Configure the active ModelUsageReporter on the global model factory."""
    from core.model_runtime.factory import get_global_model_factory

    get_global_model_factory().reporter_slot.configure(reporter)


class InMemoryModelUsageReporter:
    """In-process test reporter enforcing operation_id idempotency."""

    def __init__(self) -> None:
        self._records: dict[
            str, tuple[ModelInvocation, CanonicalTokenUsage, InvocationOutcome]
        ] = {}

    async def report(
        self,
        invocation: ModelInvocation,
        usage: CanonicalTokenUsage,
        outcome: InvocationOutcome,
    ) -> None:
        op_id = invocation.operation_id
        current_record = (invocation, usage, outcome)
        if op_id in self._records:
            existing = self._records[op_id]
            if existing != current_record:
                raise UsageEventConflictError(
                    f"Conflicting usage report for operation_id {op_id!r}"
                )
            return
        self._records[op_id] = current_record

    @property
    def events(
        self,
    ) -> list[tuple[ModelInvocation, CanonicalTokenUsage, InvocationOutcome]]:
        return list(self._records.values())

    def clear(self) -> None:
        self._records.clear()
