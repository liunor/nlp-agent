"""Outbox routing, failure retention, and transaction boundaries."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.learning import TeachingMaterials
from core.session_context import SessionContext
from gateway.dispatch import TurnTask
from gateway.redis_transport import TurnTaskCodec
from server.application.turn_reliability import OutboxRelay, utc_now


class _UowFactory:
    def __init__(self, rows):
        self.commits = 0
        self.claim_session = AsyncMock()
        selected = MagicMock()
        selected.all.return_value = rows
        self.claim_session.scalars.return_value = selected
        self.finalize_session = AsyncMock()
        by_id = {row.id: row for row in rows}

        async def find_claim(statement):
            row = by_id[statement.compile().params["id_1"]]
            assert row.status == "publishing"
            assert row.locked_by == "relay-1"
            return row

        self.finalize_session.scalar.side_effect = find_claim

    @asynccontextmanager
    async def begin(self):
        session = self.claim_session if self.commits == 0 else self.finalize_session

        async def commit():
            self.commits += 1

        yield SimpleNamespace(session=session, commit=commit)


def _row(topic, *, payload=None, message_id="outbox-1"):
    return SimpleNamespace(
        id=message_id, topic=topic, payload_json=payload or {},
        attempts=0, status="pending", locked_by=None, locked_until=None,
    )


@pytest.mark.asyncio
async def test_mixed_batch_routes_topics_without_executing_lease_notifications():
    task = TurnTask(
        context=SessionContext(session_id="conversation-1"), turn_id="turn-1",
        content="routing regression", learning_context=None, learning_progress=None,
        exercise_state=None, teaching_materials=TeachingMaterials(),
        guided_session_id=None, exercise_session_id=None,
    )
    encoded_task = TurnTaskCodec.dumps(task)
    rows = [
        _row("turn.dispatch", payload={"task": encoded_task}, message_id="turn"),
        _row("authorization.changed", payload={"user_id": "user-1"}, message_id="auth"),
        _row("sandbox.lease.released", payload={
            "user_id": "user-1", "auth_session_id": "session-1", "reason": "logout",
        }, message_id="released"),
        _row("sandbox.lease.revoked", payload={"user_id": "user-2"}, message_id="revoked"),
    ]
    factory = _UowFactory(rows)
    redis = AsyncMock()
    destinations = []

    async def xadd(stream, fields, **options):
        # Both publishes occur after the claim transaction commits and before
        # the finalization transaction starts, including the new destination.
        assert factory.commits == 1
        factory.finalize_session.scalar.assert_not_awaited()
        destinations.append((stream, fields, options))
        return f"{len(destinations)}-0"

    redis.xadd.side_effect = xadd
    redis.publish.return_value = 0  # No subscriber is still a successful publish.

    published = await OutboxRelay(
        redis, stream="isolated:turns", relay_id="relay-1",
        authorization_channel="isolated:authorization",
    ).publish_batch(factory)

    assert published == 4
    assert factory.commits == 2
    assert all(row.status == "published" for row in rows)
    assert destinations[0] == ("isolated:turns", {"payload": encoded_task}, {})
    assert TurnTaskCodec.loads(destinations[0][1]["payload"]).turn_id == "turn-1"
    for destination, row in zip(destinations[1:], rows[2:], strict=True):
        stream, fields, options = destination
        assert stream == "isolated:turns:sandbox-events"
        assert fields["outbox_id"] == row.id
        assert fields["topic"] == row.topic
        assert json.loads(fields["payload"]) == row.payload_json
        assert options == {"maxlen": 10_000, "approximate": True}
    redis.publish.assert_awaited_once_with(
        "isolated:authorization", json.dumps({"user_id": "user-1"}),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("topic", [
    "sandbox.lease.released", "sandbox.lease.revoked",
])
async def test_sandbox_notification_redis_failure_preserves_retry(topic):
    row = _row(topic, payload={"user_id": "user-1"})
    factory = _UowFactory([row])
    redis = AsyncMock()
    redis.xadd.side_effect = ConnectionError("isolated Redis failure")
    before = utc_now()

    published = await OutboxRelay(
        redis, stream="turns", relay_id="relay-1",
    ).publish_batch(factory)

    assert published == 0
    assert row.status == "retry"
    assert row.attempts == 1
    assert row.available_at > before
    assert row.locked_by is None and row.locked_until is None
    redis.publish.assert_not_awaited()
    assert redis.xadd.await_args.args[0] == "turns:sandbox-events"


@pytest.mark.asyncio
@pytest.mark.parametrize("topic,payload", [
    ("future.topic", {"task": "must-not-be-dispatched"}),
    ("sandbox.lease.released.typo", {"user_id": "user-1"}),
    ("turn.dispatch", {"task": None}),
])
async def test_unknown_or_invalid_message_is_retained_while_valid_task_continues(topic, payload):
    bad = _row(topic, payload=payload, message_id="invalid")
    good = _row("turn.dispatch", payload={"task": "encoded-turn-task"}, message_id="valid")
    factory = _UowFactory([bad, good])
    redis = AsyncMock()
    redis.xadd.return_value = "1-0"

    published = await OutboxRelay(
        redis, stream="turns", relay_id="relay-1",
    ).publish_batch(factory)

    assert published == 1
    assert bad.status == "retry" and bad.attempts == 1
    assert bad.locked_by is None and bad.locked_until is None
    assert good.status == "published"
    redis.xadd.assert_awaited_once_with("turns", {"payload": "encoded-turn-task"})
    redis.publish.assert_not_awaited()
