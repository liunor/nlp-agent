import asyncio
from types import SimpleNamespace

import pytest

import gateway.core as gateway_core
from core.identity import AccessDeniedError, AuthenticatedPrincipal
from core.learning import LearningContext
from core.session_context import SessionContext
from configs.settings import settings
from gateway.contracts import (
    EvaluationContext,
    GatewayEventType,
    InjectMessageRequest,
    SubmitTurnRequest,
    TurnConflictError,
    TurnStatus,
    TeachingConfigurationError,
)
from gateway.core import BackendGateway
from gateway.dispatch import InProcessTurnDispatcher, TurnTask
from gateway.repository import GatewayRepository


class FakeSessions:
    def __init__(self):
        self.contexts = {}

    async def create(self, principal, *, workspace_id="default", channel="web"):
        principal.require_workspace(workspace_id)
        context = SessionContext.create(
            user_id=principal.user_id, workspace_id=workspace_id, channel=channel
        )
        self.contexts[context.session_id] = context
        return context

    async def resolve(self, principal, session_id):
        context = self.contexts[session_id]
        principal.require_context(context)
        return context

    async def delete(self, principal, session_id):
        context = await self.resolve(principal, session_id)
        self.contexts.pop(session_id)
        return context

    async def touch(self, principal, session_id):
        return await self.resolve(principal, session_id)


class FakeEngine:
    def __init__(self):
        self.sink = None
        self.block = None
        self.injected = []
        self.active = {}
        self.closed = False
        self.cancel_calls = []
        self.lifecycle = []
        self.contexts = []

    async def start(self, event_sink):
        self.sink = event_sink

    async def run_turn(self, context, turn_id, content):
        self.contexts.append(context)
        self.active[context.session_id] = turn_id
        await self.sink(
            turn_id,
            context.session_id,
            GatewayEventType.MESSAGE_DELTA,
            {"delta": "answer"},
        )
        if self.block is not None:
            await self.block.wait()
        self.active.pop(context.session_id, None)
        return f"final:{content}"

    async def inject(self, context, content):
        turn_id = self.active.get(context.session_id)
        if turn_id:
            self.injected.append(content)
        return turn_id

    async def cancel_turn(self, context, turn_id):
        self.cancel_calls.append(turn_id)
        self.lifecycle.append("cancel")
        self.active.pop(context.session_id, None)

    async def delete_session(self, context):
        self.active.pop(context.session_id, None)

    async def close(self):
        self.lifecycle.append("close")
        self.closed = True


class RecordingTurnDispatcher:
    def __init__(self):
        self.submissions = []

    async def submit(self, task):
        self.submissions.append(task)

    async def cancel(self, turn_id):
        return None

    async def close(self, *, force=False, grace_s=0):
        return None

    def active_count(self):
        return 0


class FailingTurnDispatcher(RecordingTurnDispatcher):
    async def submit(self, task):
        raise ConnectionError("redis unavailable")


def test_explicit_repository_bypasses_redis_runtime_dispatch(tmp_path, monkeypatch):
    """Injected repositories must keep tests/local integrations in-process."""
    monkeypatch.setattr(settings, "NLP_AGENT_GATEWAY_TRANSPORT", "redis")

    gateway = BackendGateway(
        engine=FakeEngine(),
        repository=GatewayRepository(tmp_path / "gateway.sqlite3"),
        sessions=FakeSessions(),
    )

    assert isinstance(gateway.dispatcher, InProcessTurnDispatcher)


class FlakyTurnDispatcher(RecordingTurnDispatcher):
    def __init__(self):
        super().__init__()
        self.attempts = 0

    async def submit(self, task):
        self.attempts += 1
        if self.attempts == 1:
            raise ConnectionError("redis unavailable")
        self.submissions.append(task)


class LearningEngine(FakeEngine):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def run_turn(
        self,
        context,
        turn_id,
        content,
        *,
        learning_context=None,
        learning_progress=None,
        exercise_state=None,
        teaching_materials=None,
    ):
        self.calls.append(
            (content, learning_context, learning_progress, exercise_state, teaching_materials)
        )
        self.active[context.session_id] = turn_id
        if self.block is not None:
            await self.block.wait()
        self.active.pop(context.session_id, None)
        return f"final:{content}"


class ExerciseProtocolEngine(LearningEngine):
    """A model double that returns the structured practice envelope required by Gateway."""

    async def run_turn(
        self, context, turn_id, content, *, learning_context=None, learning_progress=None,
        exercise_state=None, teaching_materials=None,
    ):
        await super().run_turn(
            context, turn_id, content, learning_context=learning_context,
            learning_progress=learning_progress, exercise_state=exercise_state,
            teaching_materials=teaching_materials,
        )
        if content == "开始两题练习":
            return '第 1 题：解释 Q、K、V。<!-- exercise-result: {"kind":"question","question":"解释 Q、K、V。"} -->'
        if content == "Q、K、V 分别用于查询、键和值":
            return ('回答正确。第 2 题：说明 softmax 的作用。'
                    '<!-- exercise-result: {"kind":"grading","matches":[{"criterion_index":0,"achieved":true,"evidence":"说明了 Q、K、V 的角色"}],"feedback":"角色说明完整。","next_question":"说明 softmax 的作用。"} -->')
        if content == "将分数归一化为注意力权重":
            return ('回答正确，练习完成。'
                    '<!-- exercise-result: {"kind":"grading","matches":[{"criterion_index":0,"achieved":true,"evidence":"说明了归一化作用"}],"feedback":"掌握良好。"} -->')
        return await super().run_turn(
            context,
            turn_id,
            content,
            learning_context=learning_context,
            learning_progress=learning_progress,
            exercise_state=exercise_state,
            teaching_materials=teaching_materials,
        )


@pytest.fixture
def principal():
    return AuthenticatedPrincipal(
        user_id="alice", workspace_ids=frozenset({"w1"}), roles=frozenset({"student"})
    )


@pytest.mark.asyncio
async def test_gateway_rejects_student_teaching_catalog_updates(tmp_path, principal):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    gateway = BackendGateway(
        engine=FakeEngine(),
        repository=repository,
        sessions=FakeSessions(),
        dispatcher=RecordingTurnDispatcher(),
    )

    with pytest.raises(AccessDeniedError, match="learning:content:manage"):
        await gateway.update_teaching_catalog(
            principal,
            "w1",
            {
                "workspace_id": "w1",
                "topics": [],
                "exercise_blueprints": [],
                "review_blueprints": [],
                "guided_blueprints": [],
            },
        )


@pytest.mark.asyncio
async def test_gateway_rejects_guest_teaching_catalog_reads(tmp_path):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    gateway = BackendGateway(
        engine=FakeEngine(),
        repository=repository,
        sessions=FakeSessions(),
        dispatcher=RecordingTurnDispatcher(),
    )
    guest = AuthenticatedPrincipal(
        user_id="guest",
        workspace_ids=frozenset({"w1"}),
        roles=frozenset({"guest"}),
    )

    with pytest.raises(AccessDeniedError, match="learning:content:read_workspace"):
        await gateway.get_teaching_catalog(guest, "w1")


@pytest.mark.asyncio
async def test_gateway_submits_persisted_turn_to_dispatcher(tmp_path, principal):
    engine = FakeEngine()
    sessions = FakeSessions()
    dispatcher = RecordingTurnDispatcher()
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    gateway = BackendGateway(
        engine=engine,
        repository=repository,
        sessions=sessions,
        dispatcher=dispatcher,
    )
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")

    accepted = await gateway.submit_turn(
        principal,
        SubmitTurnRequest(session_id=session.session_id, content="dispatch me"),
    )

    assert len(dispatcher.submissions) == 1
    task = dispatcher.submissions[0]
    assert isinstance(task, TurnTask)
    assert task.turn_id == accepted.turn_id
    assert task.context == session
    assert task.content == "dispatch me"
    assert repository.get_turn(accepted.turn_id).status == TurnStatus.ACCEPTED
    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_carries_an_allowed_model_profile_to_the_dispatcher(
    tmp_path, principal, monkeypatch
):
    class ProfileConfig:
        @staticmethod
        def profile(name):
            if name != "qwen":
                raise KeyError(name)
            return SimpleNamespace(label="Qwen")

    factory = SimpleNamespace(
        config=ProfileConfig(),
        profile_available=lambda name: name == "qwen",
    )
    monkeypatch.setattr(
        "core.model_runtime.factory.get_global_model_factory", lambda: factory
    )
    dispatcher = RecordingTurnDispatcher()
    gateway = BackendGateway(
        engine=FakeEngine(),
        repository=GatewayRepository(tmp_path / "gateway.sqlite3"),
        sessions=FakeSessions(),
        dispatcher=dispatcher,
    )
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")

    await gateway.submit_turn(
        principal,
        SubmitTurnRequest(
            session_id=session.session_id,
            content="use qwen",
            model_profile="qwen",
        ),
    )

    assert dispatcher.submissions[0].model_profile == "qwen"
    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_marks_turn_failed_when_dispatch_transport_rejects_it(
    tmp_path, principal
):
    sessions = FakeSessions()
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    gateway = BackendGateway(
        engine=FakeEngine(),
        repository=repository,
        sessions=sessions,
        dispatcher=FailingTurnDispatcher(),
    )
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")

    with pytest.raises(ConnectionError, match="redis unavailable"):
        await gateway.submit_turn(
            principal,
            SubmitTurnRequest(session_id=session.session_id, content="dispatch me"),
        )

    failed = repository.active_turn_for_session(session.session_id)
    assert failed is None
    failed_turn = repository.list_turns(session.session_id)[0]
    assert failed_turn.error_kind == "dispatch_failed"
    events = repository.events_after(failed_turn.turn_id)
    assert [event.type for event in events][-1] == GatewayEventType.TURN_FAILED
    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_retries_dispatch_failure_for_same_idempotency_key(
    tmp_path, principal
):
    sessions = FakeSessions()
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    dispatcher = FlakyTurnDispatcher()
    gateway = BackendGateway(
        engine=FakeEngine(),
        repository=repository,
        sessions=sessions,
        dispatcher=dispatcher,
    )
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")
    request = SubmitTurnRequest(
        session_id=session.session_id,
        content="dispatch me",
        idempotency_key="same",
    )

    with pytest.raises(ConnectionError):
        await gateway.submit_turn(principal, request)
    retried = await gateway.submit_turn(principal, request)

    assert retried.duplicate is True
    assert dispatcher.attempts == 2
    assert dispatcher.submissions[0].turn_id == retried.turn_id
    assert repository.get_turn(retried.turn_id).status == TurnStatus.ACCEPTED
    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_rejects_changed_content_when_retrying_an_idempotency_key(
    tmp_path, principal
):
    sessions = FakeSessions()
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    dispatcher = FlakyTurnDispatcher()
    gateway = BackendGateway(
        engine=FakeEngine(),
        repository=repository,
        sessions=sessions,
        dispatcher=dispatcher,
    )
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")

    with pytest.raises(ConnectionError):
        await gateway.submit_turn(
            principal,
            SubmitTurnRequest(
                session_id=session.session_id,
                content="original request",
                idempotency_key="same",
            ),
        )

    with pytest.raises(TurnConflictError, match="idempotency key"):
        await gateway.submit_turn(
            principal,
            SubmitTurnRequest(
                session_id=session.session_id,
                content="different request",
                idempotency_key="same",
            ),
        )

    original = repository.list_turns(session.session_id)[0]
    assert original.input_text == "original request"
    assert dispatcher.attempts == 1
    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_attachment_path_is_session_relative_and_retry_is_idempotent(
    tmp_path, principal, monkeypatch
):
    sessions = FakeSessions()
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    dispatcher = RecordingTurnDispatcher()
    uploads_root = tmp_path / ".data" / "uploads"
    monkeypatch.setattr(gateway_core, "_DEFAULT_UPLOADS_ROOT", uploads_root)
    gateway = BackendGateway(
        engine=FakeEngine(),
        repository=repository,
        sessions=sessions,
        dispatcher=dispatcher,
    )
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")
    upload_dir = uploads_root / "w1" / "alice" / session.session_id
    upload_dir.mkdir(parents=True)
    (upload_dir / "safe-image.png").write_bytes(b"uploaded")
    request = SubmitTurnRequest(
        session_id=session.session_id,
        content="",
        attachments=[{"file_name": "safe-image.png"}],
        idempotency_key="attachment-request",
    )

    accepted = await gateway.submit_turn(principal, request)
    duplicate = await gateway.submit_turn(principal, request)

    expected = (
        "---附件---\n"
        "[图片] safe-image.png\n"
        "路径: safe-image.png\n"
        "---附件结束---"
    )
    assert duplicate.duplicate is True
    assert duplicate.turn_id == accepted.turn_id
    assert dispatcher.submissions[0].content == expected
    assert repository.get_turn(accepted.turn_id).input_text == expected
    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_runs_turn_replays_events_and_deduplicates(tmp_path, principal):
    engine = FakeEngine()
    sessions = FakeSessions()
    gateway = BackendGateway(
        engine=engine,
        repository=GatewayRepository(tmp_path / "gateway.sqlite3"),
        sessions=sessions,
        shutdown_grace_s=0,
    )
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")
    request = SubmitTurnRequest(
        session_id=session.session_id, content="hello", idempotency_key="same"
    )
    accepted = await gateway.submit_turn(principal, request)
    events = [event async for event in gateway.stream_events(principal, accepted.turn_id)]

    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert events[-1].type == GatewayEventType.TURN_COMPLETED
    turn = await gateway.get_turn(principal, accepted.turn_id)
    assert turn.status == TurnStatus.COMPLETED
    assert turn.final_text == "final:hello"

    duplicate = await gateway.submit_turn(principal, request)
    assert duplicate.duplicate is True
    assert duplicate.turn_id == accepted.turn_id
    await gateway.close()
    assert engine.closed is True


@pytest.mark.asyncio
async def test_gateway_passes_evaluation_batch_labels_to_the_engine_context(tmp_path, principal):
    engine = FakeEngine()
    gateway = BackendGateway(
        engine=engine,
        repository=GatewayRepository(tmp_path / "gateway.sqlite3"),
        sessions=FakeSessions(),
        shutdown_grace_s=0,
    )
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")

    accepted = await gateway.submit_turn(
        principal,
        SubmitTurnRequest(
            session_id=session.session_id,
            content="evaluate this case",
            evaluation=EvaluationContext(run_id="run-1", suite_id="suite-1", case_id="case-1"),
        ),
    )
    _ = [event async for event in gateway.stream_events(principal, accepted.turn_id)]

    assert engine.contexts[-1].observability_attributes == {
        "evaluation_run_id": "run-1",
        "evaluation_suite_id": "suite-1",
        "evaluation_case_id": "case-1",
    }
    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_enforces_single_turn_supports_injection_and_cancel(tmp_path, principal):
    engine = FakeEngine()
    engine.block = asyncio.Event()
    sessions = FakeSessions()
    gateway = BackendGateway(
        engine=engine,
        repository=GatewayRepository(tmp_path / "gateway.sqlite3"),
        sessions=sessions,
    )
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")
    first = await gateway.submit_turn(
        principal, SubmitTurnRequest(session_id=session.session_id, content="slow")
    )
    while session.session_id not in engine.active:
        await asyncio.sleep(0)

    with pytest.raises(TurnConflictError):
        await gateway.submit_turn(
            principal, SubmitTurnRequest(session_id=session.session_id, content="second")
        )
    injected = await gateway.inject_message(
        principal,
        InjectMessageRequest(session_id=session.session_id, content="correction"),
    )
    assert injected.turn_id == first.turn_id
    assert engine.injected == ["correction"]

    cancelled = await gateway.cancel_turn(principal, first.turn_id)
    assert cancelled.status == TurnStatus.CANCELLED
    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_denies_cross_user_turn_access(tmp_path, principal):
    gateway = BackendGateway(
        engine=FakeEngine(),
        repository=GatewayRepository(tmp_path / "gateway.sqlite3"),
        sessions=FakeSessions(),
    )
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")
    accepted = await gateway.submit_turn(
        principal, SubmitTurnRequest(session_id=session.session_id, content="private")
    )
    bob = AuthenticatedPrincipal(user_id="bob", workspace_ids=frozenset({"w1"}))
    with pytest.raises(AccessDeniedError):
        await gateway.get_turn(bob, accepted.turn_id)
    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_serializes_concurrent_turn_submission_per_session(tmp_path, principal):
    engine = FakeEngine()
    engine.block = asyncio.Event()
    sessions = FakeSessions()
    gateway = BackendGateway(
        engine=engine,
        repository=GatewayRepository(tmp_path / "gateway.sqlite3"),
        sessions=sessions,
        shutdown_grace_s=0,
    )
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")

    results = await asyncio.gather(
        gateway.submit_turn(
            principal,
            SubmitTurnRequest(session_id=session.session_id, content="first"),
        ),
        gateway.submit_turn(
            principal,
            SubmitTurnRequest(session_id=session.session_id, content="second"),
        ),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, TurnConflictError) for result in results) == 1
    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_shutdown_allows_active_turn_to_finish_within_grace(tmp_path, principal):
    engine = FakeEngine()
    engine.block = asyncio.Event()
    sessions = FakeSessions()
    gateway = BackendGateway(
        engine=engine,
        repository=GatewayRepository(tmp_path / "gateway.sqlite3"),
        sessions=sessions,
        shutdown_grace_s=0.5,
    )
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")
    accepted = await gateway.submit_turn(
        principal, SubmitTurnRequest(session_id=session.session_id, content="finish")
    )
    while session.session_id not in engine.active:
        await asyncio.sleep(0)

    close_task = asyncio.create_task(gateway.close())
    await asyncio.sleep(0.01)
    engine.block.set()
    await close_task

    assert engine.cancel_calls == []
    assert engine.lifecycle == ["close"]
    reopened = GatewayRepository(tmp_path / "gateway.sqlite3")
    assert reopened.get_turn(accepted.turn_id).status == TurnStatus.COMPLETED
    reopened.close()


@pytest.mark.asyncio
async def test_gateway_shutdown_cancels_turn_after_grace_before_engine_close(
    tmp_path, principal
):
    engine = FakeEngine()
    engine.block = asyncio.Event()
    sessions = FakeSessions()
    gateway = BackendGateway(
        engine=engine,
        repository=GatewayRepository(tmp_path / "gateway.sqlite3"),
        sessions=sessions,
        shutdown_grace_s=0.01,
    )
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")
    accepted = await gateway.submit_turn(
        principal, SubmitTurnRequest(session_id=session.session_id, content="blocked")
    )
    while session.session_id not in engine.active:
        await asyncio.sleep(0)

    await gateway.close()

    assert engine.cancel_calls == [accepted.turn_id]
    assert engine.lifecycle == ["cancel", "close"]
    reopened = GatewayRepository(tmp_path / "gateway.sqlite3")
    assert reopened.get_turn(accepted.turn_id).status == TurnStatus.CANCELLED
    reopened.close()


@pytest.mark.asyncio
async def test_delete_session_serializes_with_new_turn_submission(tmp_path, principal):
    class DeletingEngine(FakeEngine):
        def __init__(self):
            super().__init__()
            self.delete_started = asyncio.Event()
            self.allow_delete = asyncio.Event()

        async def delete_session(self, context):
            self.delete_started.set()
            await self.allow_delete.wait()

    engine = DeletingEngine()
    sessions = FakeSessions()
    gateway = BackendGateway(
        engine=engine,
        repository=GatewayRepository(tmp_path / "gateway.sqlite3"),
        sessions=sessions,
    )
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")

    deleting = asyncio.create_task(gateway.delete_session(principal, session.session_id))
    await engine.delete_started.wait()
    submitting = asyncio.create_task(
        gateway.submit_turn(
            principal,
            SubmitTurnRequest(session_id=session.session_id, content="too late"),
        )
    )
    await asyncio.sleep(0)
    assert not submitting.done()

    engine.allow_delete.set()
    await deleting
    result = await asyncio.gather(submitting, return_exceptions=True)
    assert isinstance(result[0], (KeyError, AccessDeniedError))
    assert gateway.repository.list_turns(session.session_id) == []
    await gateway.close()


@pytest.mark.asyncio
async def test_terminal_event_stream_replays_every_page(tmp_path, principal):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    gateway = BackendGateway(engine=FakeEngine(), repository=repository, sessions=FakeSessions())
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")
    turn, _ = repository.create_turn(
        turn_id="large-turn",
        session_id=session.session_id,
        workspace_id="w1",
        user_id=principal.user_id,
        input_text="large",
        idempotency_key=None,
    )
    for sequence in range(2005):
        repository.append_event(
            turn_id=turn.turn_id,
            session_id=session.session_id,
            event_type=GatewayEventType.MESSAGE_DELTA,
            payload={"index": sequence},
        )
    repository.update_turn(turn.turn_id, TurnStatus.COMPLETED, final_text="done")

    events = [event async for event in gateway.stream_events(principal, turn.turn_id)]

    assert len(events) == 2005
    assert events[-1].sequence == 2005
    await gateway.close()


@pytest.mark.asyncio
async def test_stream_finishes_when_turn_completes_during_history_replay(tmp_path, principal):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    gateway = BackendGateway(engine=FakeEngine(), repository=repository, sessions=FakeSessions())
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")
    turn, _ = repository.create_turn(
        turn_id="finishing-turn", session_id=session.session_id, workspace_id="w1",
        user_id=principal.user_id, input_text="race", idempotency_key=None,
    )
    repository.update_turn(turn.turn_id, TurnStatus.RUNNING)
    for index in range(2000):
        repository.append_event(
            turn_id=turn.turn_id, session_id=session.session_id,
            event_type=GatewayEventType.MESSAGE_DELTA, payload={"index": index},
        )
    original_replay = gateway.replay_events
    replay_calls = 0

    async def replay_and_complete(*args, **kwargs):
        nonlocal replay_calls
        events = await original_replay(*args, **kwargs)
        replay_calls += 1
        if replay_calls == 1:
            repository.append_event(
                turn_id=turn.turn_id, session_id=session.session_id,
                event_type=GatewayEventType.TURN_COMPLETED,
            )
            repository.update_turn(turn.turn_id, TurnStatus.COMPLETED, final_text="done")
        return events

    gateway.replay_events = replay_and_complete
    events = await asyncio.wait_for(
        _collect_stream(gateway, principal, turn.turn_id), timeout=2
    )

    assert len(events) == 2001
    assert events[-1].type == GatewayEventType.TURN_COMPLETED
    await gateway.close()


@pytest.mark.asyncio
async def test_live_stream_repairs_event_gap_larger_than_one_page(tmp_path, principal):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    gateway = BackendGateway(engine=FakeEngine(), repository=repository, sessions=FakeSessions())
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")
    turn, _ = repository.create_turn(
        turn_id="gapped-turn", session_id=session.session_id, workspace_id="w1",
        user_id=principal.user_id, input_text="gap", idempotency_key=None,
    )
    repository.update_turn(turn.turn_id, TurnStatus.RUNNING)
    collecting = asyncio.create_task(_collect_stream(gateway, principal, turn.turn_id, max_queue=1))
    await asyncio.sleep(0)
    for index in range(2504):
        repository.append_event(
            turn_id=turn.turn_id, session_id=session.session_id,
            event_type=GatewayEventType.MESSAGE_DELTA, payload={"index": index},
        )
    repository.update_turn(turn.turn_id, TurnStatus.COMPLETED, final_text="done")
    await gateway._emit(
        turn.turn_id, session.session_id, GatewayEventType.TURN_COMPLETED,
        {"status": TurnStatus.COMPLETED.value},
    )

    events = await asyncio.wait_for(collecting, timeout=3)
    assert [event.sequence for event in events] == list(range(1, 2506))
    await gateway.close()


async def _collect_stream(gateway, principal, turn_id, **kwargs):
    return [event async for event in gateway.stream_events(principal, turn_id, **kwargs)]


@pytest.mark.asyncio
async def test_switching_topic_starts_fresh_progress_and_blueprint(tmp_path, principal):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    repository.update_teaching_catalog("w1", {
        "workspace_id": "w1",
        "topics": [
            {"id": "a", "name": "Topic A", "description": "A desc", "status": "enabled", "knowledge_points": []},
            {"id": "b", "name": "Topic B", "description": "B desc", "status": "enabled", "knowledge_points": [{"id": "kp-b", "name": "B point", "markdown": "B body", "status": "enabled", "sort_order": 0}]},
        ],
        "exercise_blueprints": [
            {"id": "bp-a", "name": "A", "topic_id": "a", "level": "beginner", "instructions": "ask A", "question_types": ["text"], "question_count": 1, "status": "enabled", "knowledge_point_ids": [], "rubric": [{"criterion": "A rubric"}]},
            {"id": "bp-b", "name": "B", "topic_id": "b", "level": "beginner", "instructions": "ask B", "question_types": ["text"], "question_count": 1, "status": "enabled", "knowledge_point_ids": ["kp-b"], "rubric": [{"criterion": "B rubric"}]},
        ],
        "review_blueprints": [],
    })
    engine = LearningEngine()
    gateway = BackendGateway(engine=engine, repository=repository, sessions=FakeSessions())
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")

    for topic_id, topic_name in (("a", "Topic A"), ("b", "Topic B")):
        accepted = await gateway.submit_turn(principal, SubmitTurnRequest(
            session_id=session.session_id,
            content=f"learn {topic_id}",
            learning_context=LearningContext(topic_id=topic_id, topic_name=topic_name, mode="practice"),
        ))
        _ = [event async for event in gateway.stream_events(principal, accepted.turn_id)]

    _, context, progress, exercise, materials = engine.calls[-1]
    assert context.topic_id == "b"
    assert progress.objective == "学习并掌握Topic B"
    assert exercise.blueprint_id == "bp-b"
    assert exercise.rubric == ["B rubric"]
    assert materials.learning_topic["description"] == "B desc"
    assert materials.learning_topic["knowledge_points"] == ["B body"]
    assert materials.exercise_blueprint["instructions"] == "ask B"
    await gateway.close()


@pytest.mark.asyncio
async def test_practice_level_does_not_filter_enabled_blueprints(tmp_path, principal):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    repository.update_teaching_catalog("w1", {
        "workspace_id": "w1",
        "topics": [{"id": "topic", "name": "Topic", "description": "", "status": "enabled", "knowledge_points": []}],
        "exercise_blueprints": [
            {"id": "legacy-beginner", "name": "Legacy beginner", "topic_id": "topic", "level": "beginner", "instructions": "ask this question", "question_types": [], "question_count": 1, "status": "enabled", "knowledge_point_ids": [], "rubric": []},
        ],
        "review_blueprints": [],
    })
    engine = LearningEngine()
    gateway = BackendGateway(engine=engine, repository=repository, sessions=FakeSessions())
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")
    accepted = await gateway.submit_turn(principal, SubmitTurnRequest(
        session_id=session.session_id, content="advanced",
        learning_context=LearningContext(topic_id="topic", topic_name="Topic", mode="practice", level="advanced"),
    ))
    _ = [event async for event in gateway.stream_events(principal, accepted.turn_id)]

    assert engine.calls[0][3].blueprint_id == "legacy-beginner"
    assert engine.calls[0][1].level == "advanced"
    assert engine.calls[0][4].exercise_blueprint["instructions"] == "ask this question"
    await gateway.close()


@pytest.mark.asyncio
async def test_disabled_topic_is_rejected_before_creating_a_turn(tmp_path, principal):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    repository.update_teaching_catalog("w1", {
        "workspace_id": "w1",
        "topics": [{"id": "retired", "name": "已停用主题", "description": "", "status": "disabled", "knowledge_points": []}],
        "exercise_blueprints": [], "review_blueprints": [],
    })
    engine = LearningEngine()
    gateway = BackendGateway(engine=engine, repository=repository, sessions=FakeSessions())
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")

    with pytest.raises(TeachingConfigurationError, match="主题不可用"):
        await gateway.submit_turn(principal, SubmitTurnRequest(
            session_id=session.session_id, content="继续学习",
            learning_context=LearningContext(topic_id="retired", topic_name="已停用主题"),
        ))

    assert repository.list_turns(session.session_id) == []
    assert engine.calls == []
    await gateway.close()


@pytest.mark.asyncio
async def test_practice_without_an_enabled_blueprint_is_rejected_before_creating_a_turn(tmp_path, principal):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    repository.update_teaching_catalog("w1", {
        "workspace_id": "w1",
        "topics": [{"id": "attention", "name": "Attention", "description": "", "status": "enabled", "knowledge_points": []}],
        "exercise_blueprints": [], "review_blueprints": [], "guided_blueprints": [],
    })
    engine = LearningEngine()
    gateway = BackendGateway(engine=engine, repository=repository, sessions=FakeSessions())
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")

    with pytest.raises(TeachingConfigurationError, match="尚未配置练习蓝图"):
        await gateway.submit_turn(principal, SubmitTurnRequest(
            session_id=session.session_id, content="开始练习",
            learning_context=LearningContext(topic_id="attention", topic_name="Attention", mode="practice"),
        ))

    assert repository.list_turns(session.session_id) == []
    assert repository.active_exercise_session(session_id=session.session_id, topic_id="attention", mode="practice") is None
    assert engine.calls == []
    await gateway.close()


@pytest.mark.asyncio
async def test_socratic_mode_creates_resumes_and_advances_an_isolated_guided_session(tmp_path, principal):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    repository.update_teaching_catalog("w1", {
        "workspace_id": "w1",
        "topics": [{"id": "attention", "name": "Attention", "description": "", "status": "enabled", "knowledge_points": [{"id": "qkv", "name": "QKV", "markdown": "", "status": "enabled", "sort_order": 0}]}],
        "exercise_blueprints": [], "review_blueprints": [],
        "guided_blueprints": [{"id": "guided-qkv", "name": "从 QKV 角色开始", "topic_id": "attention", "knowledge_point_id": "qkv", "guidance": "先让学生区分 Q、K、V 的职责，再追问注意力权重。", "status": "enabled"}],
    })
    engine = LearningEngine()
    gateway = BackendGateway(engine=engine, repository=repository, sessions=FakeSessions())
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")
    context = LearningContext(topic_id="attention", topic_name="Attention", mode="socratic")

    first = await gateway.submit_turn(principal, SubmitTurnRequest(
        session_id=session.session_id, content="我想理解注意力机制", learning_context=context,
    ))
    _ = [event async for event in gateway.stream_events(principal, first.turn_id)]
    first_snapshot = engine.calls[-1][4].guided_session

    assert first_snapshot["objective"] == "我想理解注意力机制"
    assert first_snapshot["attempts"] == 0
    assert first_snapshot["status"] == "active"
    assert engine.calls[-1][4].guided_blueprint["id"] == "guided-qkv"
    assert engine.calls[-1][4].guided_blueprint["guidance"].startswith("先让学生")
    active = repository.active_guided_session(session_id=session.session_id, topic_id="attention")
    assert active is not None
    assert active["last_question"] == "final:我想理解注意力机制"

    second = await gateway.submit_turn(principal, SubmitTurnRequest(
        session_id=session.session_id, content="我觉得是词之间的关系", learning_context=context,
    ))
    _ = [event async for event in gateway.stream_events(principal, second.turn_id)]
    second_snapshot = engine.calls[-1][4].guided_session

    assert second_snapshot["id"] == first_snapshot["id"]
    assert second_snapshot["attempts"] == 1
    assert second_snapshot["learner_responses"] == ["我觉得是词之间的关系"]
    assert engine.calls[-1][4].guided_blueprint["id"] == "guided-qkv"
    await gateway.close()


@pytest.mark.asyncio
async def test_leaving_socratic_mode_ends_guided_session_without_changing_chat_session(tmp_path, principal):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    engine = LearningEngine()
    sessions = FakeSessions()
    gateway = BackendGateway(engine=engine, repository=repository, sessions=sessions)
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")

    guided = await gateway.submit_turn(principal, SubmitTurnRequest(
        session_id=session.session_id, content="带我理解反向传播",
        learning_context=LearningContext(topic_id=None, topic_name="", mode="socratic"),
    ))
    _ = [event async for event in gateway.stream_events(principal, guided.turn_id)]
    assert repository.active_guided_session(session_id=session.session_id, topic_id="") is not None

    explain = await gateway.submit_turn(principal, SubmitTurnRequest(
        session_id=session.session_id, content="直接解释定义",
        learning_context=LearningContext(topic_id=None, topic_name="", mode="explain"),
    ))
    _ = [event async for event in gateway.stream_events(principal, explain.turn_id)]

    assert repository.active_guided_session(session_id=session.session_id, topic_id="") is None
    assert sessions.contexts[session.session_id].session_id == session.session_id
    assert engine.calls[-1][4].guided_session == {}
    await gateway.close()


@pytest.mark.asyncio
async def test_practice_chat_persists_one_question_attempt_score_and_evidence_per_blueprint(tmp_path, principal):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    repository.update_teaching_catalog("w1", {
        "workspace_id": "w1",
        "topics": [{"id": "attention", "name": "Attention", "description": "", "status": "enabled", "knowledge_points": []}],
        "exercise_blueprints": [{
            "id": "attention-practice", "name": "两题练习", "topic_id": "attention", "level": "beginner",
            "instructions": "逐题练习", "question_types": ["简答"], "question_count": 2, "status": "enabled",
            "knowledge_point_ids": ["qkv"], "rubric": [{"criterion": "准确说明核心作用", "weight": 2}],
        }],
        "review_blueprints": [],
    })
    engine = ExerciseProtocolEngine()
    gateway = BackendGateway(engine=engine, repository=repository, sessions=FakeSessions())
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")
    context = LearningContext(topic_id="attention", topic_name="Attention", mode="practice")

    for content in ("开始两题练习", "Q、K、V 分别用于查询、键和值"):
        accepted = await gateway.submit_turn(principal, SubmitTurnRequest(
            session_id=session.session_id, content=content, learning_context=context,
        ))
        _ = [event async for event in gateway.stream_events(principal, accepted.turn_id)]

    exercise = repository.active_or_latest_exercise_session(
        session_id=session.session_id, topic_id="attention", mode="practice"
    )
    assert exercise["status"] == "completed"
    assert [item["question"] for item in repository.exercise_questions(exercise["id"])] == ["解释 Q、K、V。"]
    attempts = repository.exercise_attempts(exercise["id"])
    assert [(attempt["answer"], attempt["normalized_score"], attempt["passed"]) for attempt in attempts] == [
        ("Q、K、V 分别用于查询、键和值", 100, True),
    ]
    evidence = repository.learning_evidence(exercise["id"])
    assert len(evidence) == 1
    assert evidence[0]["knowledge_point_ids"] == ["qkv"]
    follow_up = await gateway.submit_turn(principal, SubmitTurnRequest(
        session_id=session.session_id, content="谢谢", learning_context=context,
    ))
    _ = [event async for event in gateway.stream_events(principal, follow_up.turn_id)]
    assert repository.active_or_latest_exercise_session(
        session_id=session.session_id, topic_id="attention", mode="practice"
    )["id"] == exercise["id"]
    assert engine.calls[1][3].question == "解释 Q、K、V。"
    await gateway.close()


@pytest.mark.asyncio
async def test_leaving_practice_cancels_the_unfinished_exercise_without_affecting_chat_session(tmp_path, principal):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    repository.update_teaching_catalog("w1", {
        "workspace_id": "w1",
        "topics": [{"id": "attention", "name": "Attention", "description": "", "status": "enabled", "knowledge_points": []}],
        "exercise_blueprints": [{
            "id": "attention-practice", "name": "练习", "topic_id": "attention", "level": "beginner",
            "instructions": "逐题练习", "question_types": ["简答"], "question_count": 2, "status": "enabled",
            "knowledge_point_ids": [], "rubric": [],
        }], "review_blueprints": [],
    })
    engine = ExerciseProtocolEngine()
    sessions = FakeSessions()
    gateway = BackendGateway(engine=engine, repository=repository, sessions=sessions)
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")
    practice = LearningContext(topic_id="attention", topic_name="Attention", mode="practice")

    started = await gateway.submit_turn(principal, SubmitTurnRequest(
        session_id=session.session_id, content="开始两题练习", learning_context=practice,
    ))
    _ = [event async for event in gateway.stream_events(principal, started.turn_id)]
    switched = await gateway.submit_turn(principal, SubmitTurnRequest(
        session_id=session.session_id, content="直接讲解 attention", learning_context=LearningContext(
            topic_id="attention", topic_name="Attention", mode="explain"
        ),
    ))
    _ = [event async for event in gateway.stream_events(principal, switched.turn_id)]

    exercise = repository.active_or_latest_exercise_session(
        session_id=session.session_id, topic_id="attention", mode="practice"
    )
    assert exercise["status"] == "cancelled"
    assert sessions.contexts[session.session_id].session_id == session.session_id
    await gateway.close()


@pytest.mark.asyncio
async def test_conflicting_turn_does_not_become_latest_learning_state(tmp_path, principal):
    engine = LearningEngine()
    engine.block = asyncio.Event()
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    gateway = BackendGateway(engine=engine, repository=repository, sessions=FakeSessions())
    await gateway.start()
    session = await gateway.create_session(principal, workspace_id="w1")
    first = await gateway.submit_turn(principal, SubmitTurnRequest(
        session_id=session.session_id,
        content="topic A",
        learning_context=LearningContext(topic_id="a", topic_name="Topic A", mode="explain"),
    ))
    while session.session_id not in engine.active:
        await asyncio.sleep(0)

    with pytest.raises(TurnConflictError):
        await gateway.submit_turn(principal, SubmitTurnRequest(
            session_id=session.session_id,
            content="rejected topic B",
            learning_context=LearningContext(topic_id="b", topic_name="Topic B", mode="review"),
        ))
    await gateway.cancel_turn(principal, first.turn_id)
    engine.block = None
    accepted = await gateway.submit_turn(
        principal, SubmitTurnRequest(session_id=session.session_id, content="continue")
    )
    _ = [event async for event in gateway.stream_events(principal, accepted.turn_id)]

    assert engine.calls[-1][1].topic_id == "a"
    await gateway.close()
