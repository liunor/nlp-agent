from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.application.turn_reliability import (
    CancelledTurnClaim,
    LostTurnClaimError,
    TurnReliabilityService,
    utc_now,
)
from server.infrastructure.mysql.models import TurnCancellationModel, TurnModel


@pytest.mark.asyncio
@pytest.mark.parametrize("tampered", [False, True])
async def test_revoked_dispatch_releases_only_the_matching_durable_task(monkeypatch, tampered):
    from dataclasses import replace
    from core.learning import TeachingMaterials
    from core.session_context import SessionContext
    from gateway.dispatch import ExecutionAuthorizationContext, TurnTask
    from gateway.redis_transport import TurnTaskCodec

    task = TurnTask(
        context=SessionContext(session_id="s", user_id="u"), turn_id="t", content="hi",
        learning_context=None, learning_progress=None, exercise_state=None,
        teaching_materials=TeachingMaterials(), guided_session_id=None, exercise_session_id=None,
        authorization=ExecutionAuthorizationContext("u", "default", 3),
    )
    turn = TurnModel(id="t", conversation_id="s", workspace_id="default", user_id="u",
                     input_text="hi", status="running", claim_generation=2, claimed_by="w",
                     learning_state_json={"dispatch_task": TurnTaskCodec.dumps(task)})
    session = AsyncMock()
    session.scalar.return_value = turn
    finish = AsyncMock()
    monkeypatch.setattr("server.application.turn_reliability.finish_async", finish)
    delivered = replace(task, authorization=ExecutionAuthorizationContext("u", "default", 9)) if tampered else task

    rejected = await TurnReliabilityService().reject_revoked_task(
        session, task=delivered, generation=2, worker_id="w"
    )

    assert rejected is (not tampered)
    if tampered:
        assert turn.status == "running"
        finish.assert_not_awaited()
    else:
        assert turn.status == "failed"
        assert turn.claimed_by is None
        assert turn.lease_expires_at is None
        assert turn.error_kind == "authorization_revoked"
        finish.assert_awaited_once_with(session, turn)


@pytest.mark.asyncio
async def test_claim_increments_generation_and_heartbeat_requires_the_same_owner() -> None:
    turn = TurnModel(id="turn-1", conversation_id="conversation-1", workspace_id="workspace-1", user_id="user-1", input_text="hi", status="accepted", claim_generation=0)
    session = AsyncMock()
    session.add = MagicMock()
    session.scalar.side_effect = [turn, None, turn, None, turn]
    service = TurnReliabilityService()

    generation = await service.claim_turn(session, turn_id="turn-1", worker_id="worker-a", lease_s=30)
    assert generation == 1
    await service.heartbeat(session, turn_id="turn-1", generation=1, worker_id="worker-a", lease_s=30)

    with pytest.raises(LostTurnClaimError):
        await service.heartbeat(session, turn_id="turn-1", generation=0, worker_id="worker-a", lease_s=30)


@pytest.mark.asyncio
async def test_recovery_invalidates_old_generation_and_emits_handover_without_resetting_sequence(monkeypatch) -> None:
    from server.application import turn_reliability

    class _Quota:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def reserve(self, *args, **kwargs):
            return object()

        async def finalize(self, *args, **kwargs) -> None:
            return None

    monkeypatch.setattr(turn_reliability, "AsyncStorageQuota", _Quota)
    turn = TurnModel(
        id="turn-1",
        conversation_id="conversation-1",
        workspace_id="workspace-1",
        user_id="user-1",
        input_text="hi",
        status="running",
        claim_generation=2,
        lease_expires_at=utc_now() - timedelta(seconds=1),
    )
    session = AsyncMock()
    session.add = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = [turn]
    latest_sequence = MagicMock()
    latest_sequence.first.return_value = 7
    session.scalars.side_effect = [scalars, latest_sequence]
    recovered_dispatch = {"turn_id": "turn-1", "task": "encoded-turn-task"}
    session.scalar.side_effect = [recovered_dispatch, turn, None]
    service = TurnReliabilityService()

    recovered = await service.recover_stuck_turns(session)

    assert recovered == ["turn-1"]
    assert turn.claim_generation == 3
    assert turn.status == "accepted"
    assert turn.claimed_by is None
    assert turn.lease_expires_at is None
    next_generation = await service.claim_turn(
        session, turn_id="turn-1", worker_id="worker-b", lease_s=30
    )
    assert next_generation == 4
    assert turn.claimed_by == "worker-b"
    added = [call.args[0] for call in session.add.call_args_list]
    assert any(getattr(item, "event_type", None) == "turn.handover" and getattr(item, "sequence", None) == 8 for item in added)
    assert any(
        getattr(item, "topic", None) == "turn.dispatch"
        and getattr(item, "payload_json", None) == {"turn_id": "turn-1", "task": "encoded-turn-task"}
        for item in added
    )


@pytest.mark.asyncio
async def test_claim_reports_durable_cancellation_as_a_distinct_outcome() -> None:
    turn = TurnModel(
        id="turn-1",
        conversation_id="conversation-1",
        workspace_id="workspace-1",
        user_id="user-1",
        input_text="hi",
        status="accepted",
        claim_generation=0,
    )
    cancellation = TurnCancellationModel(
        turn_id="turn-1", requested_by="user-1", reason="user_requested"
    )
    session = AsyncMock()
    session.scalar.side_effect = [turn, cancellation]
    service = TurnReliabilityService()

    result = await service.claim_turn(
        session, turn_id="turn-1", worker_id="worker-a", lease_s=30
    )

    assert isinstance(result, CancelledTurnClaim)
    assert turn.status == "cancelled"
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_repairs_legacy_unleased_zombie_and_preserves_sequence(monkeypatch) -> None:
    from server.application import turn_reliability

    class _Quota:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def reserve(self, *args, **kwargs):
            return object()

        async def finalize(self, *args, **kwargs) -> None:
            return None

    monkeypatch.setattr(turn_reliability, "AsyncStorageQuota", _Quota)
    turn = TurnModel(
        id="turn-1",
        conversation_id="conversation-1",
        workspace_id="workspace-1",
        user_id="user-1",
        input_text="hi",
        status="running",
        claim_generation=2,
        claimed_by=None,
        lease_expires_at=None,
    )
    session = AsyncMock()
    session.add = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = [turn]
    latest_sequence = MagicMock()
    latest_sequence.first.return_value = 7
    session.scalars.side_effect = [scalars, latest_sequence]
    recovered_dispatch = {"turn_id": "turn-1", "task": "encoded-turn-task"}
    session.scalar.side_effect = [recovered_dispatch, turn, None]
    service = TurnReliabilityService()

    recovered = await service.recover_stuck_turns(session)

    assert recovered == ["turn-1"]
    assert turn.claim_generation == 3
    assert turn.status == "accepted"
    assert turn.claimed_by is None
    assert turn.heartbeat_at is None
    assert turn.lease_expires_at is None
    recovery_query = str(session.scalars.await_args_list[0].args[0])
    assert "nlp_turns.claimed_by IS NULL" in recovery_query
    assert "nlp_turns.lease_expires_at IS NULL" in recovery_query
    added = [call.args[0] for call in session.add.call_args_list]
    assert any(getattr(item, "event_type", None) == "turn.handover" and getattr(item, "sequence", None) == 8 for item in added)
    assert any(
        getattr(item, "topic", None) == "turn.dispatch"
        and getattr(item, "payload_json", None) == {"turn_id": "turn-1", "task": "encoded-turn-task"}
        for item in added
    )

    session.scalar.side_effect = [turn, None]
    generation = await service.claim_turn(
        session,
        turn_id="turn-1",
        worker_id="worker-new",
        lease_s=30,
    )
    assert generation == 4
    assert turn.status == "running"
    assert turn.claimed_by == "worker-new"
    assert turn.lease_expires_at is not None


@pytest.mark.asyncio
async def test_recovery_exhaustion_converges_turn_to_failed_terminal_state(monkeypatch) -> None:
    expired_at = utc_now() - timedelta(seconds=1)
    turn = TurnModel(
        id="turn-1",
        conversation_id="conversation-1",
        workspace_id="workspace-1",
        user_id="user-1",
        input_text="hi",
        status="running",
        claim_generation=6,
        claimed_by="worker-old",
        heartbeat_at=expired_at,
        lease_expires_at=expired_at,
    )
    session = AsyncMock()
    session.add = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = [turn]
    session.scalars.return_value = scalars
    finish = AsyncMock()
    monkeypatch.setattr("server.application.turn_reliability.finish_async", finish)

    recovered = await TurnReliabilityService().recover_stuck_turns(
        session, max_retries=3
    )

    assert recovered == []
    assert turn.status == "failed"
    assert turn.error_kind == "turn_lease_recovery_exhausted"
    assert turn.completed_at is not None
    assert turn.claimed_by is None
    assert turn.heartbeat_at is None
    assert turn.lease_expires_at is None
    finish.assert_awaited_once_with(session, turn)


@pytest.mark.asyncio
async def test_operation_replay_uses_stable_turn_operation_identity() -> None:
    operation = AsyncMock()
    session = AsyncMock()
    session.add = MagicMock()
    session.scalar.return_value = operation

    restored = await TurnReliabilityService().record_operation(session, turn_id="turn-1", generation=2, operation_id="tool-1", tool_name="send_message", request={})

    assert restored is operation
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_outbox_relay_publishes_turn_task_in_worker_stream_format() -> None:
    from server.application.turn_reliability import OutboxRelay

    row = MagicMock(
        id="outbox-1",
        topic="turn.dispatch",
        payload_json={"task": "encoded-turn-task"},
        attempts=0,
    )
    rows = MagicMock()
    rows.all.return_value = [row]
    claim_session = AsyncMock()
    claim_session.scalars.return_value = rows
    finalize_session = AsyncMock()
    finalize_session.scalar.return_value = row

    class _UnitOfWork:
        def __init__(self, session):
            self.session = session
            self.committed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def commit(self):
            self.committed = True

    first_uow = _UnitOfWork(claim_session)
    second_uow = _UnitOfWork(finalize_session)

    class _UowFactory:
        def __init__(self):
            self._uows = iter((first_uow, second_uow))

        def begin(self):
            return next(self._uows)

    redis = AsyncMock()

    async def _publish(stream, fields):
        assert first_uow.committed
        assert not second_uow.committed
        assert stream == "turns"
        assert fields == {"payload": "encoded-turn-task"}
        return "1-0"

    redis.xadd.side_effect = _publish

    published = await OutboxRelay(redis, stream="turns", relay_id="relay-1").publish_batch(_UowFactory())

    assert published == 1
    redis.xadd.assert_awaited_once_with("turns", {"payload": "encoded-turn-task"})
    assert row.status == "published"
    assert first_uow.committed and second_uow.committed


@pytest.mark.asyncio
async def test_outbox_relay_retries_provider_failure_after_releasing_database_claim() -> None:
    from server.application.turn_reliability import OutboxRelay

    row = MagicMock(
        id="outbox-failed",
        topic="turn.dispatch",
        payload_json={"task": "encoded-turn-task"},
        attempts=0,
        status="pending",
    )
    claim_rows = MagicMock()
    claim_rows.all.return_value = [row]
    claim_session = AsyncMock()
    claim_session.scalars.return_value = claim_rows
    finalize_session = AsyncMock()
    finalize_session.scalar.return_value = row

    class _UnitOfWork:
        def __init__(self, session):
            self.session = session

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def commit(self):
            return None

    class _UowFactory:
        def __init__(self):
            self._uows = iter((_UnitOfWork(claim_session), _UnitOfWork(finalize_session)))

        def begin(self):
            return next(self._uows)

    redis = AsyncMock()
    redis.xadd.side_effect = ConnectionError("redis unavailable")
    before = utc_now()

    published = await OutboxRelay(redis, stream="turns", relay_id="relay-1").publish_batch(_UowFactory())

    assert published == 0
    assert row.status == "retry"
    assert row.locked_by is None
    assert row.locked_until is None
    assert row.attempts == 1
    assert row.available_at > before


@pytest.mark.asyncio
async def test_outbox_dispatcher_only_tracks_task_persisted_by_turn_repository() -> None:
    from core.learning import TeachingMaterials
    from core.session_context import SessionContext
    from gateway.dispatch import TurnTask
    from gateway.outbox_dispatcher import OutboxTurnDispatcher

    reliability = AsyncMock()
    transport = AsyncMock()
    transport.worker_heartbeats.return_value = [
        {"active_slots": 2}, {"active_slots": 1}
    ]
    task = TurnTask(
        context=SessionContext(session_id="session-1"),
        turn_id="turn-1",
        content="hello",
        learning_context=None,
        learning_progress=None,
        exercise_state=None,
        teaching_materials=TeachingMaterials(),
        guided_session_id=None,
        exercise_session_id=None,
    )

    dispatcher = OutboxTurnDispatcher(reliability, transport)
    await dispatcher.submit(task)

    reliability.enqueue.assert_not_awaited()
    transport.submit.assert_not_awaited()
    assert await dispatcher.worker_heartbeats() == [
        {"active_slots": 2}, {"active_slots": 1}
    ]
    assert dispatcher.active_count() == 0
