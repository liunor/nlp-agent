from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.learning import TeachingMaterials
from core.identity import AuthenticatedPrincipal
from core.rbac import Permission
from core.session_context import SessionContext
from gateway.dispatch import ExecutionAuthorizationContext, TurnTask
from server.application.turn_reliability import LostTurnClaimError


@pytest.mark.asyncio
async def test_fenced_executor_claims_turn_before_invoking_agent(monkeypatch) -> None:
    from server.worker.fencing import FencedTurnExecutor

    session = AsyncMock()
    unit_of_work = AsyncMock()
    unit_of_work.session = session
    unit_of_work.__aenter__.return_value = unit_of_work
    factory = MagicMock()
    factory.begin.return_value = unit_of_work
    reliability = AsyncMock()
    reliability.claim_turn.return_value = 4
    execute = AsyncMock()
    principal = AuthenticatedPrincipal(
        user_id="user-1", workspace_ids=frozenset({"default"}),
        permissions=frozenset({Permission.AGENT_TURN_SUBMIT}), authorization_version=3,
    )
    monkeypatch.setattr(
        "server.worker.fencing.rbac_service.principal_for_user_id",
        AsyncMock(return_value=principal),
    )
    task = TurnTask(
        context=SessionContext(session_id="session-1", user_id="user-1"),
        turn_id="turn-1",
        content="hello",
        learning_context=None,
        learning_progress=None,
        exercise_state=None,
        teaching_materials=TeachingMaterials(),
        guided_session_id=None,
        exercise_session_id=None,
        authorization=ExecutionAuthorizationContext("user-1", "default", 3),
    )

    claimed = await FencedTurnExecutor(factory, reliability, execute, worker_id="worker-a", lease_s=30)(task)

    assert claimed is True
    reliability.claim_turn.assert_awaited_once_with(
        session,
        turn_id="turn-1",
        worker_id="worker-a",
        lease_s=30,
        user_id="user-1",
        workspace_id="default",
    )
    unit_of_work.commit.assert_awaited_once()
    execute.assert_awaited_once()
    execution = execute.await_args.args[1]
    assert execution.turn_id == "turn-1"
    assert execution.claim_generation == 4
    assert execution.operation_id == "turn.execution"


@pytest.mark.asyncio
async def test_fenced_executor_does_not_execute_turn_lost_to_another_worker() -> None:
    from server.worker.fencing import FencedTurnExecutor

    unit_of_work = AsyncMock()
    unit_of_work.session = AsyncMock()
    unit_of_work.__aenter__.return_value = unit_of_work
    factory = MagicMock()
    factory.begin.return_value = unit_of_work
    reliability = AsyncMock()
    reliability.claim_turn.return_value = None
    execute = AsyncMock()
    task = TurnTask(
        context=SessionContext(session_id="session-1"), turn_id="turn-1", content="hello",
        learning_context=None, learning_progress=None, exercise_state=None,
        teaching_materials=TeachingMaterials(), guided_session_id=None, exercise_session_id=None,
    )

    claimed = await FencedTurnExecutor(factory, reliability, execute, worker_id="worker-a", lease_s=30)(task)

    assert claimed is False
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_fenced_executor_cancels_execution_when_heartbeat_loses_claim(
    monkeypatch,
) -> None:
    from server.worker.fencing import FencedTurnExecutor

    unit_of_work = AsyncMock()
    unit_of_work.session = AsyncMock()
    unit_of_work.__aenter__.return_value = unit_of_work
    factory = MagicMock()
    factory.begin.return_value = unit_of_work
    reliability = AsyncMock()
    reliability.claim_turn.return_value = 1
    reliability.heartbeat.side_effect = LostTurnClaimError("claim moved")
    principal = AuthenticatedPrincipal(
        user_id="user-1",
        workspace_ids=frozenset({"default"}),
        permissions=frozenset({Permission.AGENT_TURN_SUBMIT}),
        authorization_version=3,
    )
    monkeypatch.setattr(
        "server.worker.fencing.rbac_service.principal_for_user_id",
        AsyncMock(return_value=principal),
    )
    execution_cancelled = asyncio.Event()

    async def execute(_task, _context):
        try:
            await asyncio.Event().wait()
        finally:
            execution_cancelled.set()

    task = TurnTask(
        context=SessionContext(session_id="session-1", user_id="user-1"),
        turn_id="turn-1",
        content="hello",
        learning_context=None,
        learning_progress=None,
        exercise_state=None,
        teaching_materials=TeachingMaterials(),
        guided_session_id=None,
        exercise_session_id=None,
        authorization=ExecutionAuthorizationContext("user-1", "default", 3),
    )
    executor = FencedTurnExecutor(
        factory, reliability, execute, worker_id="worker-a", lease_s=3
    )
    executor._lease_s = 0.03

    with pytest.raises(LostTurnClaimError, match="claim moved"):
        await asyncio.wait_for(executor(task), timeout=0.3)

    assert execution_cancelled.is_set()


@pytest.mark.asyncio
async def test_fenced_executor_rejects_legacy_task_without_authorization_context() -> None:
    from server.worker.fencing import FencedTurnExecutor

    unit_of_work = AsyncMock()
    unit_of_work.session = AsyncMock()
    unit_of_work.__aenter__.return_value = unit_of_work
    factory = MagicMock()
    factory.begin.return_value = unit_of_work
    reliability = AsyncMock()
    reliability.claim_turn.return_value = 1
    execute = AsyncMock()
    task = TurnTask(
        context=SessionContext(session_id="session-1"), turn_id="turn-1", content="hello",
        learning_context=None, learning_progress=None, exercise_state=None,
        teaching_materials=TeachingMaterials(), guided_session_id=None, exercise_session_id=None,
    )

    with pytest.raises(PermissionError, match="lacks authorization context"):
        await FencedTurnExecutor(factory, reliability, execute, worker_id="worker-a", lease_s=30)(task)
    execute.assert_not_awaited()
