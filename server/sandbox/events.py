"""Ordered sandbox execution events; Redis transport is added behind this seam."""

from __future__ import annotations

from collections import defaultdict


class SandboxEventStore:
    """In-memory reference semantics for event IDs and replay cursors.

    Phase 1 callers depend only on this interface; deployment wiring replaces
    this storage with Redis Streams without changing ticket or UI contracts.
    """

    def __init__(self) -> None:
        self._events: dict[str, list[dict[str, object]]] = defaultdict(list)

    async def append(
        self, execution_id: str, *, user_id: str, event_type: str, payload: dict[str, object]
    ) -> dict[str, object]:
        events = self._events[execution_id]
        event = {"event_id": str(len(events) + 1), "seq": len(events) + 1, "type": event_type, "payload": payload, "user_id": user_id}
        events.append(event)
        return {key: value for key, value in event.items() if key != "user_id"}

    async def replay(self, execution_id: str, *, user_id: str, after_event_id: str | None = None) -> list[dict[str, object]]:
        after = int(after_event_id or "0")
        return [
            {key: value for key, value in event.items() if key != "user_id"}
            for event in self._events.get(execution_id, [])
            if event["user_id"] == user_id and int(str(event["event_id"])) > after
        ]
