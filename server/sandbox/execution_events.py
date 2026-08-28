"""Small, transport-independent helpers for execution event payloads."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar


_EventResult = TypeVar("_EventResult")

class SandboxEventDeliveryError(RuntimeError):
    """Raised after the execution row was fenced failed due to event failure."""

    def __init__(self, cause: BaseException) -> None:
        self.cause = cause
        super().__init__(f"sandbox event delivery failed: {cause}")


async def append_event_with_retry(
    append: Callable[[], Awaitable[_EventResult]],
    *,
    attempts: int = 3,
    base_delay_seconds: float = 0.05,
) -> _EventResult:
    """Append an event with bounded retry for transient Redis failures.

    The caller still owns the terminal-state compensation when all attempts
    fail.  Keeping retry here makes every execution path (HTTP and model tools)
    apply the same delivery policy without holding a Manager RPC transaction.
    """
    if attempts < 1:
        raise ValueError("attempts must be positive")
    last_error: BaseException | None = None
    for attempt in range(attempts):
        try:
            return await append()
        except Exception as error:
            last_error = error
            if attempt + 1 < attempts and base_delay_seconds > 0:
                await asyncio.sleep(base_delay_seconds * (2**attempt))
    assert last_error is not None
    raise last_error

def execution_output_streams(result: dict[str, object]) -> tuple[tuple[str, str], ...]:
    streams: list[tuple[str, str]] = []
    for name in ("stdout", "stderr"):
        value = result.get(name)
        if value:
            streams.append((name, str(value)))
    return tuple(streams)


def execution_failure_payload(error: BaseException) -> dict[str, str]:
    message = f"{type(error).__name__}: {error}"[:128]
    return {"error_type": type(error).__name__, "error": message}


def execution_result_failed(result: dict[str, object]) -> bool:
    return str(result.get("status") or "completed").lower() in {
        "failed",
        "error",
        "timeout",
        "timed_out",
    }


def execution_result_failure_payload(result: dict[str, object]) -> dict[str, str]:
    message = str(result.get("error") or result.get("stderr") or "sandbox execution failed")[:128]
    return {"error_type": "SandboxExecutionError", "error": message}
