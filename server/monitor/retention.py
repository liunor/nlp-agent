"""Periodic retention cleanup for the monitor-owned telemetry store."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from utils.logger import get_logger


logger = get_logger("nlp_agent.monitor.retention")


class PrunableRepository(Protocol):
    def prune(self, trace_days: int, event_days: int) -> dict[str, int]: ...


def monitor_retention_settings(config: Mapping[str, Any]) -> dict[str, int | bool]:
    section = config.get("retention")
    values = section if isinstance(section, Mapping) else {}
    return {
        "enabled": bool(values.get("enabled", True)),
        "trace_days": max(1, int(values.get("trace_days", 30))),
        "event_days": max(1, int(values.get("event_days", 30))),
        # Authorization decisions are kept longer than operational telemetry
        # because they are security evidence, while still having a bounded
        # storage policy instead of growing forever.
        "audit_days": max(30, int(values.get("audit_days", 180))),
        "interval_s": max(60, int(values.get("interval_s", 30 * 24 * 60 * 60))),
        "initial_delay_s": max(0, int(values.get("initial_delay_s", 60))),
    }


def enforce_manual_retention(
    *,
    configured_trace_days: int,
    configured_event_days: int,
    requested_trace_days: int,
    requested_event_days: int,
) -> tuple[int, int]:
    """Keep an on-demand cleanup from deleting inside the configured window.

    A caller may ask to retain data for longer, but never for less time than
    the deployment policy. This makes old clients and bookmarked API calls
    safe even when they still send the page's selected display period.
    """
    return (
        max(1, int(configured_trace_days), int(requested_trace_days)),
        max(1, int(configured_event_days), int(requested_event_days)),
    )


async def run_retention_once(
    repository: PrunableRepository, *, trace_days: int, event_days: int
) -> dict[str, int]:
    return await asyncio.to_thread(repository.prune, trace_days, event_days)


async def run_monitor_retention(
    repository: PrunableRepository,
    *,
    trace_days: int,
    event_days: int,
    initial_delay_s: int,
    interval_s: int,
    audit_cleanup: Callable[[], Awaitable[object]] | None = None,
) -> None:
    """Run retention monthly while the monitor process is alive.

    The delayed first run prevents a monitor restart from competing with normal
    startup work. Cancellation is intentionally allowed to propagate so the
    monitor lifespan can shut down without leaving a task behind.
    """
    if initial_delay_s:
        await asyncio.sleep(initial_delay_s)
    while True:
        try:
            result = await run_retention_once(
                repository, trace_days=trace_days, event_days=event_days
            )
            logger.info(
                "monitor retention cleanup removed traces=%s spans=%s events=%s",
                result.get("traces", 0),
                result.get("spans", 0),
                result.get("events", 0),
            )
            if audit_cleanup is not None:
                try:
                    audit_result = await audit_cleanup()
                    logger.info("monitor audit retention cleanup result=%s", audit_result)
                except Exception:
                    # Telemetry cleanup must continue even if the independent
                    # authorization-audit table is temporarily unavailable.
                    logger.exception("monitor audit retention cleanup failed")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("monitor retention cleanup failed")
        await asyncio.sleep(interval_s)
