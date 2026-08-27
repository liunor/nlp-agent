"""Small, transport-independent helpers for execution event payloads."""

from __future__ import annotations


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
