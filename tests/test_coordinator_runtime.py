import asyncio
import json
import time

import pytest
from langchain_core.messages import HumanMessage

from core.coordinator_runtime import CoordinatorRuntime, invoke_model_with_telemetry
from core.learning import TeachingMaterials
from core.observability.context import TelemetryContext, bind_telemetry_context
from core.observability.models import SpanKind
from core.observability.runtime import TelemetryRuntime
from core.session_context import SessionContext
from core.task_manager import global_task_manager
from core.worker_events import WorkerCompletedEvent, WorkerEventBus
from core.worker_protocol import WorkerWaitPlan
from core.agent_runtime import global_agent_injections
from schemas.models import WorkerExecutionResultSpec, WorkerTimingSpec


@pytest.fixture(autouse=True)
def clear_task_manager():
    active = global_task_manager.active_tasks
    terminal = global_task_manager.terminal_tasks
    global_task_manager.active_tasks = {}
    global_task_manager.terminal_tasks = type(terminal)()
    yield
    for task in global_task_manager.active_tasks.values():
        task.future.cancel()
    global_task_manager.active_tasks = active
    global_task_manager.terminal_tasks = terminal


def register_worker(worker_id, turn_id, *, mode="all", quorum=1, timeout=1.0):
    future = asyncio.create_task(asyncio.sleep(10))
    global_task_manager.register_task(
        worker_id,
        "research",
        "find facts",
        future,
        "session-a",
        join=True,
        parent_turn_id=turn_id,
        wait_mode=mode,
        quorum=quorum,
        wait_timeout_s=timeout,
    )
    return future


def event(worker_id, turn_id, *, event_id=None):
    now = time.time()
    return WorkerCompletedEvent.create(
        event_id=event_id,
        session_id="session-a",
        worker_id=worker_id,
        parent_turn_id=turn_id,
        attempt=1,
        execution=WorkerExecutionResultSpec(
            status="completed",
            summary="done",
            output=f"answer-{worker_id}",
            timing=WorkerTimingSpec(started_at=now, completed_at=now, duration_ms=0),
            termination_reason="completed",
        ),
        join=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "quorum", "completed_workers"),
    [("any", 1, ["w1"]), ("quorum", 2, ["w1", "w2"]), ("all", 1, ["w1", "w2", "w3"])],
)
async def test_wait_barrier_resumes_at_configured_threshold(mode, quorum, completed_workers):
    bus = WorkerEventBus()
    calls = []

    async def invoke(messages, context, background, turn_id):
        calls.append((messages, context, background, turn_id))

    runtime = CoordinatorRuntime(bus, invoke)
    futures = [register_worker(w, "turn-1", mode=mode, quorum=quorum) for w in ["w1", "w2", "w3"]]
    turn = asyncio.create_task(
        runtime.submit_user_turn("session-a", HumanMessage(content="question", id="turn-1"))
    )
    await asyncio.sleep(0)
    for worker_id in completed_workers:
        global_task_manager.transition_task(worker_id, "running", "test_started")
        global_task_manager.complete_task(worker_id, "completed")
        await bus.publish(event(worker_id, "turn-1"))
    await asyncio.wait_for(turn, 1)

    payload = json.loads(calls[-1][0][0].content.splitlines()[2])
    assert payload["barrier"]["mode"] == mode
    assert payload["barrier"]["timed_out"] is False
    assert len(payload["events"]) == len(completed_workers)
    assert calls[-1][1].session_id == "session-a"
    assert calls[-1][3] == "turn-1"
    for future in futures:
        future.cancel()
    await asyncio.gather(*futures, return_exceptions=True)
    await runtime.close()


@pytest.mark.asyncio
async def test_wait_barrier_reports_timeout_without_worker_result():
    bus = WorkerEventBus()
    calls = []

    async def invoke(messages, session_id, background, turn_id):
        calls.append(messages)

    runtime = CoordinatorRuntime(bus, invoke)
    future = register_worker("slow", "turn-timeout", timeout=0.02)
    await runtime.submit_user_turn(
        "session-a", HumanMessage(content="question", id="turn-timeout")
    )
    payload = json.loads(calls[-1][0].content.splitlines()[2])
    assert payload["barrier"]["timed_out"] is True
    assert payload["events"] == []
    future.cancel()
    await asyncio.gather(future, return_exceptions=True)
    await runtime.close()


@pytest.mark.asyncio
async def test_wait_barrier_keeps_other_turn_worker_results_out_of_current_turn():
    bus = WorkerEventBus()
    calls = []

    async def invoke(messages, context, background, turn_id):
        calls.append((messages, turn_id))

    runtime = CoordinatorRuntime(bus, invoke)
    future = register_worker("current", "turn-current")
    turn = asyncio.create_task(
        runtime.submit_user_turn(
            "session-a", HumanMessage(content="question", id="turn-current")
        )
    )
    await asyncio.sleep(0)

    await bus.publish(event("detached", "turn-previous"))
    global_task_manager.transition_task("current", "running", "test_started")
    global_task_manager.complete_task("current", "completed")
    await bus.publish(event("current", "turn-current"))
    await asyncio.wait_for(turn, 1)

    barrier_messages, barrier_turn_id = next(
        (messages, turn_id)
        for messages, turn_id in calls
        if turn_id == "turn-current"
        and str(messages[0].content).startswith("[INTERNAL_WORKER_RESULTS]")
    )
    payload = json.loads(barrier_messages[0].content.splitlines()[2])
    assert barrier_turn_id == "turn-current"
    assert [item["worker_id"] for item in payload["events"]] == ["current"]

    future.cancel()
    await asyncio.gather(future, return_exceptions=True)
    await runtime.close()


@pytest.mark.asyncio
async def test_wait_barrier_does_not_consume_detached_worker_from_same_turn():
    bus = WorkerEventBus()

    async def invoke(_messages, _context, _background, _turn_id):
        return None

    runtime = CoordinatorRuntime(bus, invoke)
    await bus.publish(event("detached", "turn-1"))
    await bus.publish(event("joined", "turn-1"))
    plan = WorkerWaitPlan(
        session_id="session-a",
        parent_turn_id="turn-1",
        worker_ids=frozenset({"joined"}),
        mode="all",
        quorum=1,
        timeout_s=1,
    )

    collected, timed_out = await runtime._collect_barrier_events_unobserved(plan)

    assert timed_out is False
    assert [item.worker_id for item in collected] == ["joined"]
    assert [item.worker_id for item in bus.drain("session-a")] == ["detached"]
    await runtime.close()


@pytest.mark.asyncio
async def test_detached_result_is_serialized_through_runtime():
    bus = WorkerEventBus()
    calls = []

    async def invoke(messages, session_id, background, turn_id):
        calls.append((background, turn_id, messages))

    runtime = CoordinatorRuntime(bus, invoke)
    await runtime.submit_user_turn(
        "session-a", HumanMessage(content="question", id="turn-detached")
    )
    detached = WorkerCompletedEvent.create(
        session_id="session-a",
        worker_id="detached",
        parent_turn_id="turn-detached",
        attempt=1,
        execution=WorkerExecutionResultSpec(
            status="completed",
            summary="done",
            output="later",
            timing=WorkerTimingSpec(
                started_at=time.time(), completed_at=time.time(), duration_ms=0
            ),
            termination_reason="completed",
        ),
        join=False,
    )
    await bus.publish(detached)
    await asyncio.sleep(0.02)
    assert calls[-1][0] is True
    assert calls[-1][1] == "turn-detached"
    await runtime.close()


@pytest.mark.asyncio
async def test_detached_results_from_different_turns_resume_separately():
    bus = WorkerEventBus()
    calls = []

    async def invoke(messages, _context, background, turn_id):
        if background:
            payload = json.loads(messages[0].content.splitlines()[2])
            calls.append((turn_id, payload))

    runtime = CoordinatorRuntime(bus, invoke)
    await bus.publish(event("worker-first", "turn-first"))
    await bus.publish(event("worker-second", "turn-second"))
    await asyncio.sleep(0.05)

    assert [(turn_id, [item["worker_id"] for item in payload["events"]]) for turn_id, payload in calls] == [
        ("turn-first", ["worker-first"]),
        ("turn-second", ["worker-second"]),
    ]
    await runtime.close()


@pytest.mark.asyncio
async def test_worker_resume_preserves_full_session_identity():
    bus = WorkerEventBus()
    contexts = []

    async def invoke(_messages, context, _background, _turn_id):
        contexts.append(context)

    runtime = CoordinatorRuntime(bus, invoke)
    scoped = SessionContext(
        session_id="session-a",
        user_id="alice",
        workspace_id="nlp",
        channel="web",
    )
    await runtime.submit_user_turn(scoped, HumanMessage(content="question", id="turn-scoped"))
    await bus.publish(event("detached", "turn-scoped"))
    await asyncio.sleep(0.02)

    assert contexts[-1] == scoped
    await runtime.close()


@pytest.mark.asyncio
async def test_new_message_is_injected_into_active_coordinator_turn():
    bus = WorkerEventBus()
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def invoke(messages, context, background, turn_id):
        calls.append(messages)
        if len(calls) == 1:
            entered.set()
            await release.wait()
        else:
            await global_agent_injections.drain(context.session_id, limit=3)

    runtime = CoordinatorRuntime(bus, invoke)
    first = asyncio.create_task(runtime.submit_user_turn(
        "session-inject", HumanMessage(content="first", id="turn-first")
    ))
    await entered.wait()

    await asyncio.wait_for(
        runtime.submit_user_turn(
            "session-inject", HumanMessage(content="follow-up", id="turn-follow")
        ),
        timeout=0.2,
    )
    assert global_agent_injections.pending("session-inject") == 1

    release.set()
    await asyncio.wait_for(first, 1)
    assert len(calls) == 2
    assert global_agent_injections.pending("session-inject") == 0
    await runtime.close()


@pytest.mark.asyncio
async def test_eight_argument_invoke_keeps_teaching_materials_compatibility():
    calls = []

    async def invoke(
        _messages, _context, _background, _turn_id, _learning_context,
        _learning_progress, _exercise_state, teaching_materials,
    ):
        calls.append(teaching_materials)

    runtime = CoordinatorRuntime(WorkerEventBus(), invoke)
    materials = TeachingMaterials(learning_topic={"id": "transformer"})
    await runtime.submit_user_turn(
        "session-materials",
        HumanMessage(content="question", id="turn-materials"),
        teaching_materials=materials,
    )

    assert calls == [materials]
    await runtime.close()


@pytest.mark.asyncio
async def test_model_invocation_records_usage_in_a_model_span(monkeypatch, tmp_path):
    telemetry = TelemetryRuntime(tmp_path / "telemetry.sqlite3", flush_interval_s=0.01)

    class Model:
        async def ainvoke(self, _messages, config=None):
            return type("Response", (), {"usage_metadata": {
                "input_tokens": 11, "output_tokens": 4, "total_tokens": 15,
            }})()

    monkeypatch.setattr("core.coordinator_runtime.global_telemetry", telemetry)
    context = TelemetryContext.create(session_id="session-model", turn_id="turn-model")
    telemetry.start_trace(context)
    with bind_telemetry_context(context):
        await invoke_model_with_telemetry(Model(), [], {}, name="coordinator.model")
    telemetry.complete_trace(context)
    await telemetry.flush()

    detail = telemetry.repository.trace_detail(context.trace_id)
    assert detail is not None
    assert detail["trace"]["total_tokens"] == 15
    assert detail["spans"][0]["kind"] == SpanKind.MODEL.value
    await telemetry.close()


@pytest.mark.asyncio
async def test_model_invocation_uses_telemetry_context_from_graph_config(monkeypatch, tmp_path):
    telemetry = TelemetryRuntime(tmp_path / "telemetry.sqlite3", flush_interval_s=0.01)

    class Model:
        async def ainvoke(self, _messages, config=None):
            return type("Response", (), {"usage_metadata": {"total_tokens": 7}})()

    monkeypatch.setattr("core.coordinator_runtime.global_telemetry", telemetry)
    context = TelemetryContext.create(session_id="config-session", turn_id="config-turn")
    telemetry.start_trace(context)
    await invoke_model_with_telemetry(
        Model(), [], {"configurable": context.configurable()}, name="coordinator.model"
    )
    telemetry.complete_trace(context)
    await telemetry.flush()

    detail = telemetry.repository.trace_detail(context.trace_id)
    assert detail is not None
    assert len(detail["spans"]) == 1
    assert detail["spans"][0]["kind"] == SpanKind.MODEL.value
    assert detail["trace"]["total_tokens"] == 7
    await telemetry.close()


@pytest.mark.asyncio
async def test_coordinator_stores_evaluation_batch_labels_on_the_root_trace(monkeypatch, tmp_path):
    telemetry = TelemetryRuntime(tmp_path / "telemetry.sqlite3", flush_interval_s=0.01)

    async def invoke(_messages, _context, _background, _turn_id):
        return None

    monkeypatch.setattr("core.coordinator_runtime.global_telemetry", telemetry)
    runtime = CoordinatorRuntime(WorkerEventBus(), invoke)
    await runtime.submit_user_turn(
        SessionContext(
            session_id="evaluation-session",
            observability_attributes={
                "evaluation_run_id": "run-1",
                "evaluation_suite_id": "suite-1",
                "evaluation_case_id": "case-1",
            },
        ),
        HumanMessage(content="case input", id="evaluation-turn"),
    )
    await telemetry.flush()

    trace = telemetry.repository.list_traces(limit=1)[0]
    assert trace["attributes"] == {
        "evaluation_run_id": "run-1",
        "evaluation_suite_id": "suite-1",
        "evaluation_case_id": "case-1",
    }
    await runtime.close()
    await telemetry.close()


@pytest.mark.asyncio
async def test_model_invocation_does_not_duplicate_usage_for_model_runtime(monkeypatch, tmp_path):
    telemetry = TelemetryRuntime(tmp_path / "telemetry.sqlite3", flush_interval_s=0.01)

    class Model:
        emits_model_telemetry = True

        async def ainvoke(self, _messages, config=None):
            return type("Response", (), {"usage_metadata": {"total_tokens": 15}})()

    monkeypatch.setattr("core.coordinator_runtime.global_telemetry", telemetry)
    context = TelemetryContext.create(session_id="session-model", turn_id="turn-model")
    telemetry.start_trace(context)
    with bind_telemetry_context(context):
        await invoke_model_with_telemetry(Model(), [], {}, name="coordinator.model")
    telemetry.complete_trace(context)
    await telemetry.flush()

    detail = telemetry.repository.trace_detail(context.trace_id)
    assert detail is not None
    assert detail["trace"]["total_tokens"] == 0
    assert detail["spans"] == []
    await telemetry.close()
