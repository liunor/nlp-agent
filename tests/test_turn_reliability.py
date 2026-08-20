from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.application.turn_reliability import LostTurnClaimError, TurnReliabilityService, utc_now
from server.infrastructure.mysql.models import TurnModel


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
async def test_recovery_invalidates_old_generation_and_emits_handover_without_resetting_sequence() -> None:
    turn = TurnModel(id="turn-1", conversation_id="conversation-1", workspace_id="workspace-1", user_id="user-1", input_text="hi", status="running", claim_generation=2, lease_expires_at=utc_now() - timedelta(seconds=1))
    session = AsyncMock()
    session.add = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = [turn]
    latest_sequence = MagicMock()
    latest_sequence.first.return_value = 7
    session.scalars.side_effect = [scalars, latest_sequence]
    session.scalar.return_value = {"turn_id": "turn-1", "task": "encoded-turn-task"}
    service = TurnReliabilityService()

    recovered = await service.recover_stuck_turns(session)

    assert recovered == ["turn-1"]
    assert turn.claim_generation == 3
    added = [call.args[0] for call in session.add.call_args_list]
    assert any(getattr(item, "event_type", None) == "turn.handover" and getattr(item, "sequence", None) == 8 for item in added)
    assert any(
        getattr(item, "topic", None) == "turn.dispatch"
        and getattr(item, "payload_json", None) == {"turn_id": "turn-1", "task": "encoded-turn-task"}
        for item in added
    )


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
    session = AsyncMock()
    session.scalars.return_value = rows
    redis = AsyncMock()
    redis.xadd.return_value = "1-0"

    published = await OutboxRelay(redis, stream="turns", relay_id="relay-1").publish_batch(session)

    assert published == 1
    redis.xadd.assert_awaited_once_with("turns", {"payload": "encoded-turn-task"})
    assert row.status == "published"


@pytest.mark.asyncio
async def test_outbox_dispatcher_only_tracks_task_persisted_by_turn_repository() -> None:
    from core.learning import TeachingMaterials
    from core.session_context import SessionContext
    from gateway.dispatch import TurnTask
    from gateway.outbox_dispatcher import OutboxTurnDispatcher

    reliability = AsyncMock()
    transport = AsyncMock()
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
    assert dispatcher.active_count() == 1
