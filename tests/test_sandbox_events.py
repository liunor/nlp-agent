from __future__ import annotations

import asyncio


def test_execution_events_are_ordered_and_replayable_from_last_event_id() -> None:
    from server.sandbox.events import SandboxEventStore

    async def exercise():
        events = SandboxEventStore()
        first = await events.append("exec-1", user_id="user-a", event_type="execution.started", payload={})
        await events.append("exec-1", user_id="user-a", event_type="execution.output", payload={"text": "42"})
        return first, await events.replay("exec-1", user_id="user-a", after_event_id=first["event_id"])

    first, replay = asyncio.run(exercise())
    assert first["seq"] == 1
    assert replay == [{"event_id": "2", "seq": 2, "type": "execution.output", "payload": {"text": "42"}}]
