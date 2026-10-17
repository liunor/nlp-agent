"""Worker lifecycle, failure, retry, and resource-budget primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


WorkerLifecycleStatus = Literal[
    "pending",
    "running",
    "waiting",
    "retrying",
    "cancelling",
    "completed",
    "failed",
    "cancelled",
    "timed_out",
]
WorkerTerminalStatus = Literal["completed", "failed", "cancelled", "timed_out"]
WorkerFailureCategory = Literal[
    "timeout",
    "model",
    "rate_limit",
    "network",
    "tool",
    "context",
    "budget",
    "cancelled",
    "internal",
]
WorkerTerminationReason = Literal[
    "completed",
    "cancelled",
    "timeout",
    "max_turns",
    "token_budget",
    "tool_budget",
    "unrecoverable_error",
    "retries_exhausted",
]


TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "cancelled", "timed_out"}
)

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"running", "cancelling", "cancelled", "failed"}),
    "running": frozenset(
        {
            "waiting",
            "retrying",
            "cancelling",
            "completed",
            "failed",
            "cancelled",
            "timed_out",
        }
    ),
    "waiting": frozenset(
        {"running", "retrying", "cancelling", "failed", "cancelled", "timed_out"}
    ),
    "retrying": frozenset(
        {"running", "cancelling", "failed", "cancelled", "timed_out"}
    ),
    "cancelling": frozenset({"cancelled", "failed"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "timed_out": frozenset(),
}


class InvalidWorkerTransition(ValueError):
    """Raised when a Worker attempts an illegal lifecycle transition."""


def validate_transition(current: str, target: str) -> None:
    if current == target:
        return
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise InvalidWorkerTransition(f"Invalid Worker transition: {current} -> {target}")


def detached_worker_allowed(*, join: bool, parent_turn_is_fenced: bool) -> bool:
    """A fenced Turn may only start Workers that finish before their parent does."""

    return join or not parent_turn_is_fenced


@dataclass(frozen=True, slots=True)
class WorkerResourceBudget:
    max_turns: int = 6
    max_duration_s: float = 60.0
    max_tokens: int = 32_000
    max_tool_calls: int = 12
    max_injections: int = 15
    injection_batch_size: int = 3
    max_tool_result_chars: int = 50_000
    finalize_on_exhaustion: bool = True

    def __post_init__(self) -> None:
        if self.max_turns < 1:
            raise ValueError("max_turns must be at least 1")
        if self.max_duration_s <= 0:
            raise ValueError("max_duration_s must be positive")
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")
        if self.max_tool_calls < 0:
            raise ValueError("max_tool_calls cannot be negative")
        if self.max_injections < 0:
            raise ValueError("max_injections cannot be negative")
        if self.injection_batch_size < 1:
            raise ValueError("injection_batch_size must be at least 1")
        if self.max_tool_result_chars < 512:
            raise ValueError("max_tool_result_chars must be at least 512")


@dataclass(frozen=True, slots=True)
class WorkerRetryPolicy:
    max_attempts: int = 3
    base_delay_s: float = 0.5
    max_delay_s: float = 5.0
    retryable_categories: frozenset[str] = field(
        default_factory=lambda: frozenset({"timeout", "rate_limit", "network", "model"})
    )

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay_s < 0 or self.max_delay_s < 0:
            raise ValueError("retry delays cannot be negative")

    def delay_for(self, completed_attempts: int) -> float:
        return min(self.max_delay_s, self.base_delay_s * (2 ** max(0, completed_attempts - 1)))

    def should_retry(self, category: str, attempt: int) -> bool:
        return attempt < self.max_attempts and category in self.retryable_categories


def classify_worker_error(error: BaseException) -> tuple[WorkerFailureCategory, bool]:
    """Classify provider/runtime failures without depending on provider SDK types."""

    name = type(error).__name__.lower()
    message = str(error).lower()
    text = f"{name} {message}"
    if isinstance(error, TimeoutError) or "timeout" in text or "timed out" in text:
        return "timeout", True
    if "ratelimit" in text or "rate limit" in text or "429" in text:
        return "rate_limit", True
    if any(token in text for token in ("connection", "network", "dns", "temporarily unavailable")):
        return "network", True
    if any(token in text for token in ("context length", "context_length", "maximum context")):
        return "context", False
    if any(token in text for token in ("apierror", "model", "service unavailable", "503")):
        return "model", True
    return "internal", False
