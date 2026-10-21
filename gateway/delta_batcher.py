"""Coalesce adjacent text fragments before durable event writes.

The first fragment in each channel is immediate. Subsequent fragments flush on
size/elapsed-time checks, channel changes, semantic events, or normal stream end.
There is no independent writer task that could outlive a cancelled Turn.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

from gateway.contracts import GatewayEventType


class DeltaBatcher:
    def __init__(
        self,
        sink: Callable[[GatewayEventType, dict], Awaitable[None]],
        *,
        interval_s: float = 0.1,
        max_chars: int = 128,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._sink = sink
        self._interval_s = interval_s
        self._max_chars = max_chars
        self._clock = clock
        self._last_flush = clock()
        self._seen: set[tuple[Any, bool]] = set()
        self._pending: dict | None = None

    async def emit(self, event_type: GatewayEventType, payload: dict) -> None:
        if (event_type != GatewayEventType.MESSAGE_DELTA
                or not isinstance(payload.get("delta"), str)
                or not set(payload) <= {"delta", "channel", "background"}):
            await self.flush()
            await self._sink(event_type, payload)
            return
        metadata = {k: v for k, v in payload.items() if k != "delta"}
        if not payload["delta"]:
            return
        if self._pending is not None and metadata != {k: v for k, v in self._pending.items() if k != "delta"}:
            await self.flush()
        key = (payload.get("channel"), bool(payload.get("background")))
        if key not in self._seen:
            await self._sink(event_type, payload)
            if payload["delta"].strip():
                self._seen.add(key)
            self._last_flush = self._clock()
            return
        if self._pending is None:
            self._pending = dict(payload)
        else:
            self._pending["delta"] += payload["delta"]
        if len(self._pending["delta"]) >= self._max_chars or self._clock() - self._last_flush >= self._interval_s:
            await self.flush()

    async def flush(self) -> None:
        if self._pending is not None:
            payload, self._pending = self._pending, None
            await self._sink(GatewayEventType.MESSAGE_DELTA, payload)
            self._last_flush = self._clock()
