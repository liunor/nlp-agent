import asyncio
import threading
from types import SimpleNamespace

import pytest

from core.learning import TeachingMaterials
from core.session_context import SessionContext
from gateway.contracts import GatewayEventType, TurnStatus
from gateway.dispatch import TurnTask
from gateway.turn_execution import InProcessTurnExecutor


class SuccessfulEngine:
    async def run_turn(self, _context, _turn_id, _content):
        return 'answer<!-- guided-result: {"status":"completed"} -->'

    async def cancel_turn(self, _context, _turn_id):
        return None


@pytest.mark.parametrize("explicit_timeout,expected", [(None, 270), (3, 3)])
async def test_image_turn_deadline_reaches_engine_and_respects_explicit_override(explicit_timeout, expected):
    from core.agent_runtime import configured_budget
    from core.vision_execution import current_image_turn

    observed = []
    class Engine(SuccessfulEngine):
        async def run_turn(self, _context, _turn_id, _content):
            observed.append((current_image_turn().image_count, configured_budget("coordinator").max_duration_s))
            return "four image results"

    async def emit(*args):
        pass

    task = TurnTask(
        context=SessionContext(session_id="images"), turn_id="turn-images",
        content="---附件---\n" + "\n".join(f"[图片] {i}.png\n路径: {i}.png" for i in range(4)) + "\n---附件结束---",
        learning_context=None, learning_progress=None, exercise_state=None,
        teaching_materials=TeachingMaterials(), guided_session_id=None, exercise_session_id=None,
    )
    executor = InProcessTurnExecutor(Engine(), ClaimAwareRepository(), emit, turn_timeout_s=explicit_timeout)
    text, _ = await executor._run_turn_with_timeout(task)
    assert text == "four image results"
    assert observed == [(4, expected)]
    assert current_image_turn() is None


class HangingEngine:
    def __init__(self):
        self.cancelled = []
        self._never = asyncio.Event()

    async def run_turn(self, _context, _turn_id, _content):
        await self._never.wait()
        return "unreachable"

    async def cancel_turn(self, _context, turn_id):
        self.cancelled.append(turn_id)


class CancellationIgnoringEngine(HangingEngine):
    def __init__(self):
        super().__init__()
        self.release = asyncio.Event()
        self.cancellation_attempts = 0

    async def run_turn(self, _context, _turn_id, _content):
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancellation_attempts += 1
        return "late answer"


class FailingLearningRepository:
    def __init__(self):
        self.statuses = []

    def update_turn(self, _turn_id, status, **_changes):
        self.statuses.append(status)

    def advance_guided_session(self, _guided_session_id, **_changes):
        raise RuntimeError("learning store unavailable")


class CompletionPersistenceBlockingRepository(FailingLearningRepository):
    def __init__(self):
        super().__init__()
        self.completed_started = threading.Event()
        self.release_completed = threading.Event()

    def update_turn(self, _turn_id, status, **_changes):
        self.statuses.append(status)
        if status == TurnStatus.COMPLETED:
            self.completed_started.set()
            self.release_completed.wait()
        return SimpleNamespace(status=status)


class LearningFinalizationBlockingRepository(FailingLearningRepository):
    def __init__(self):
        super().__init__()
        self.learning_started = threading.Event()
        self.release_learning = threading.Event()

    def advance_guided_session(self, _guided_session_id, **_changes):
        self.learning_started.set()
        self.release_learning.wait()

    def update_turn_guided_status(self, _turn_id, **_changes):
        return None


class CancelledAfterExecutionRepository:
    def __init__(self):
        self.statuses = []
        self.ensured_events = []

    def update_turn(self, _turn_id, status, **_changes):
        self.statuses.append(status)
        if status == TurnStatus.COMPLETED:
            return SimpleNamespace(status=TurnStatus.CANCELLED)
        return SimpleNamespace(status=status)

    def ensure_event(self, **event):
        self.ensured_events.append(event)
        return None


class ClaimAwareRepository:
    def __init__(self):
        self.updates = []

    def update_turn(self, _turn_id, status, **changes):
        self.updates.append((status, changes))
        return SimpleNamespace(status=status)


@pytest.mark.asyncio
async def test_turn_state_mutations_are_fenced_by_claim_generation():
    repository = ClaimAwareRepository()

    async def emit(_turn_id, _session_id, _event_type, _payload):
        return None

    executor = InProcessTurnExecutor(SuccessfulEngine(), repository, emit)
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

    await executor.run(task, SimpleNamespace(claim_generation=7))

    assert [status for status, _changes in repository.updates] == [
        TurnStatus.RUNNING,
        TurnStatus.COMPLETED,
    ]
    assert all(
        changes["expected_claim_generation"] == 7
        for _status, changes in repository.updates
    )


@pytest.mark.asyncio
async def test_learning_finalization_failure_moves_running_turn_to_failed():
    repository = FailingLearningRepository()
    events = []

    async def emit(_turn_id, _session_id, event_type, payload):
        events.append((event_type, payload))

    executor = InProcessTurnExecutor(SuccessfulEngine(), repository, emit)
    task = TurnTask(
        context=SessionContext(session_id="session-1"),
        turn_id="turn-1",
        content="hello",
        learning_context=None,
        learning_progress=None,
        exercise_state=None,
        teaching_materials=TeachingMaterials(),
        guided_session_id="guided-1",
        exercise_session_id=None,
    )

    await executor.run(task)

    assert repository.statuses == [TurnStatus.RUNNING, TurnStatus.FAILED]
    assert [event_type for event_type, _payload in events] == [
        GatewayEventType.TURN_STARTED,
        GatewayEventType.TURN_FAILED,
    ]
    assert events[-1][1]["error_kind"] == "RuntimeError"


@pytest.mark.asyncio
async def test_late_completion_does_not_emit_completion_after_repository_preserves_cancelled():
    repository = CancelledAfterExecutionRepository()
    events = []

    async def emit(_turn_id, _session_id, event_type, payload):
        events.append((event_type, payload))

    executor = InProcessTurnExecutor(SuccessfulEngine(), repository, emit)
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

    await executor.run(task)

    assert GatewayEventType.MESSAGE_COMPLETED not in [event_type for event_type, _ in events]
    assert GatewayEventType.TURN_COMPLETED not in [event_type for event_type, _ in events]
    assert [event["event_type"] for event in repository.ensured_events] == [GatewayEventType.TURN_CANCELLED]


@pytest.mark.asyncio
async def test_engine_timeout_converges_hanging_turn_to_failed_terminal_event():
    repository = FailingLearningRepository()
    events = []

    async def emit(_turn_id, _session_id, event_type, payload):
        events.append((event_type, payload))

    engine = HangingEngine()
    executor = InProcessTurnExecutor(
        engine, repository, emit, turn_timeout_s=0.05
    )
    task = TurnTask(
        context=SessionContext(session_id="session-1"),
        turn_id="turn-timeout",
        content="hello",
        learning_context=None,
        learning_progress=None,
        exercise_state=None,
        teaching_materials=TeachingMaterials(),
        guided_session_id=None,
        exercise_session_id=None,
    )

    await executor.run(task)

    assert repository.statuses == [TurnStatus.RUNNING, TurnStatus.FAILED]
    assert engine.cancelled == ["turn-timeout"]
    assert [event_type for event_type, _payload in events] == [
        GatewayEventType.TURN_STARTED,
        GatewayEventType.TURN_FAILED,
    ]
    assert events[-1][1]["error_kind"] == "TurnExecutionTimeoutError"


@pytest.mark.asyncio
async def test_timeout_emits_failed_terminal_event_when_engine_ignores_cancellation():
    repository = FailingLearningRepository()
    events = []

    async def emit(_turn_id, _session_id, event_type, payload):
        events.append((event_type, payload))

    engine = CancellationIgnoringEngine()
    executor = InProcessTurnExecutor(
        engine, repository, emit, turn_timeout_s=0.05
    )
    task = TurnTask(
        context=SessionContext(session_id="session-1"),
        turn_id="turn-unresponsive-cancel",
        content="hello",
        learning_context=None,
        learning_progress=None,
        exercise_state=None,
        teaching_materials=TeachingMaterials(),
        guided_session_id=None,
        exercise_session_id=None,
    )
    execution = asyncio.create_task(executor.run(task))

    try:
        await asyncio.wait_for(asyncio.shield(execution), timeout=0.6)
    finally:
        engine.release.set()
        await asyncio.wait_for(execution, timeout=0.2)

    assert engine.cancellation_attempts >= 1
    assert engine.cancelled == ["turn-unresponsive-cancel"]
    assert repository.statuses == [TurnStatus.RUNNING, TurnStatus.FAILED]
    assert [event_type for event_type, _payload in events] == [
        GatewayEventType.TURN_STARTED,
        GatewayEventType.TURN_FAILED,
    ]


@pytest.mark.asyncio
async def test_total_deadline_covers_blocked_completion_persistence():
    repository = CompletionPersistenceBlockingRepository()
    events = []

    async def emit(_turn_id, _session_id, event_type, payload):
        events.append((event_type, payload))

    executor = InProcessTurnExecutor(
        SuccessfulEngine(), repository, emit, turn_timeout_s=0.05
    )
    task = TurnTask(
        context=SessionContext(session_id="session-1"),
        turn_id="turn-completion-persistence-timeout",
        content="hello",
        learning_context=None,
        learning_progress=None,
        exercise_state=None,
        teaching_materials=TeachingMaterials(),
        guided_session_id=None,
        exercise_session_id=None,
    )
    execution = asyncio.create_task(executor.run(task))

    try:
        await asyncio.wait_for(asyncio.to_thread(repository.completed_started.wait), timeout=0.2)
        await asyncio.wait_for(asyncio.shield(execution), timeout=0.6)
    finally:
        repository.release_completed.set()
        await asyncio.wait_for(execution, timeout=0.2)

    assert repository.statuses == [
        TurnStatus.RUNNING,
        TurnStatus.COMPLETED,
        TurnStatus.FAILED,
    ]
    assert [event_type for event_type, _payload in events] == [
        GatewayEventType.TURN_STARTED,
        GatewayEventType.TURN_FAILED,
    ]


@pytest.mark.asyncio
async def test_total_deadline_covers_blocked_learning_finalization():
    repository = LearningFinalizationBlockingRepository()
    events = []

    async def emit(_turn_id, _session_id, event_type, payload):
        events.append((event_type, payload))

    executor = InProcessTurnExecutor(
        SuccessfulEngine(), repository, emit, turn_timeout_s=0.05
    )
    task = TurnTask(
        context=SessionContext(session_id="session-1"),
        turn_id="turn-learning-finalization-timeout",
        content="hello",
        learning_context=None,
        learning_progress=None,
        exercise_state=None,
        teaching_materials=TeachingMaterials(),
        guided_session_id="guided-1",
        exercise_session_id=None,
    )
    execution = asyncio.create_task(executor.run(task))

    try:
        await asyncio.wait_for(asyncio.to_thread(repository.learning_started.wait), timeout=0.2)
        await asyncio.wait_for(asyncio.shield(execution), timeout=0.6)
    finally:
        repository.release_learning.set()
        await asyncio.wait_for(execution, timeout=0.2)

    assert repository.statuses == [TurnStatus.RUNNING, TurnStatus.FAILED]
    assert [event_type for event_type, _payload in events] == [
        GatewayEventType.TURN_STARTED,
        GatewayEventType.TURN_FAILED,
    ]
