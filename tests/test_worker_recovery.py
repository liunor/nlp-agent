import asyncio
import json
import time

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from core.task_manager import global_task_manager
from core.session_context import SessionContext
from core.tool_runtime import (
    ToolDescriptor,
    ToolExecutionResult,
    ToolGrantRequest,
    ToolRisk,
    ToolRuntime,
    ToolScope,
    ToolSource,
)
from core.worker_lifecycle import WorkerResourceBudget, WorkerRetryPolicy
from schemas.models import (
    WorkerErrorSpec,
    WorkerExecutionResultSpec,
    WorkerTimingSpec,
    WorkerUsageSpec,
)
from server.tools import worker_tool
from server.tools.worker_tool import _execute_sandbox_loop, _execute_with_retries


def execution(*, status, attempt, category=None, retryable=False):
    now = time.time()
    return WorkerExecutionResultSpec(
        status=status,
        summary=status,
        output="done" if status == "completed" else None,
        error=(
            WorkerErrorSpec(category=category, message=category, retryable=retryable)
            if category
            else None
        ),
        usage=WorkerUsageSpec(total_tokens=10, tool_uses=1, duration_ms=1),
        timing=WorkerTimingSpec(started_at=now, completed_at=now, duration_ms=1),
        termination_reason="completed" if status == "completed" else "timeout",
        attempt=attempt,
    )


@pytest.fixture(autouse=True)
def isolate_global_manager():
    active = global_task_manager.active_tasks
    terminal = global_task_manager.terminal_tasks
    metrics = global_task_manager.metrics
    global_task_manager.active_tasks = {}
    global_task_manager.terminal_tasks = type(terminal)()
    yield
    for task in global_task_manager.active_tasks.values():
        task.future.cancel()
    global_task_manager.active_tasks = active
    global_task_manager.terminal_tasks = terminal
    global_task_manager.metrics = metrics


@pytest.mark.asyncio
async def test_recoverable_failures_retry_with_backoff_and_aggregate_usage():
    future = asyncio.create_task(asyncio.sleep(10))
    global_task_manager.register_task(
        "w1",
        "research",
        "task",
        future,
        "s1",
        retry_policy=WorkerRetryPolicy(max_attempts=3, base_delay_s=0.1),
    )
    global_task_manager.transition_task("w1", "running", "started")
    calls = []
    delays = []

    async def execute_attempt(attempt):
        calls.append(attempt)
        if len(calls) < 3:
            return execution(status="timed_out", attempt=attempt, category="timeout", retryable=True)
        return execution(status="completed", attempt=attempt)

    async def fake_sleep(delay):
        delays.append(delay)

    result = await _execute_with_retries("w1", execute_attempt, sleep=fake_sleep)
    assert calls == [1, 2, 3]
    assert delays == [0.1, 0.2]
    assert result.status == "completed"
    assert result.attempt == 3
    assert result.usage.total_tokens == 30
    assert result.usage.tool_uses == 3
    assert [item["status"] for item in global_task_manager.task_timeline("w1")].count("retrying") == 2
    future.cancel()
    await asyncio.gather(future, return_exceptions=True)


@pytest.mark.asyncio
async def test_non_retryable_failure_stops_immediately():
    future = asyncio.create_task(asyncio.sleep(10))
    global_task_manager.register_task("w2", "research", "task", future, "s1")
    global_task_manager.transition_task("w2", "running", "started")
    calls = []

    async def execute_attempt(attempt):
        calls.append(attempt)
        return execution(status="failed", attempt=attempt, category="budget", retryable=False).model_copy(
            update={"termination_reason": "token_budget"}
        )

    result = await _execute_with_retries("w2", execute_attempt)
    assert calls == [1]
    assert result.status == "failed"
    assert result.termination_reason == "token_budget"
    future.cancel()
    await asyncio.gather(future, return_exceptions=True)


@pytest.mark.asyncio
async def test_sandbox_enforces_token_budget(monkeypatch):
    class FakeLLM:
        async def ainvoke(self, _messages):
            return AIMessage(
                content="oversized",
                usage_metadata={"input_tokens": 6, "output_tokens": 5, "total_tokens": 11},
            )

    monkeypatch.setattr(worker_tool, "get_tool_llm", lambda: FakeLLM())
    monkeypatch.setattr(worker_tool, "record_sidechain_transcript", lambda *_args: None)
    result = await _execute_sandbox_loop(
        "budget-worker",
        "s1",
        [],
        [],
        budget=WorkerResourceBudget(max_tokens=10),
    )

    assert result.status == "failed"
    assert result.termination_reason == "token_budget"
    assert result.error.category == "budget"


@pytest.mark.asyncio
async def test_worker_context_preserves_parent_user_and_workspace(monkeypatch):
    captured = []

    class FakeContextManager:
        async def prepare(self, context, messages, _budget):
            captured.append(context)
            return type(
                "Transform",
                (),
                {
                    "messages": messages,
                    "tokens_before": 0,
                    "tokens_after": 0,
                    "actions": [],
                },
            )()

    class FakeLLM:
        async def ainvoke(self, _messages):
            return AIMessage(content="done")

    monkeypatch.setattr(worker_tool, "global_context_manager", FakeContextManager())
    monkeypatch.setattr(worker_tool, "get_tool_llm", lambda: FakeLLM())
    monkeypatch.setattr(worker_tool, "record_sidechain_transcript", lambda *_args: None)
    parent = SessionContext(
        session_id="s1",
        user_id="alice",
        workspace_id="nlp",
        channel="web",
    )

    result = await _execute_sandbox_loop(
        "worker-1",
        "s1",
        [],
        [],
        context=parent,
    )

    assert result.status == "completed"
    assert captured == [
        SessionContext(
            session_id="s1",
            user_id="alice",
            workspace_id="nlp",
            channel="worker",
            agent_id="worker-1",
        )
    ]


@pytest.mark.asyncio
async def test_worker_passes_safe_identity_and_redacts_non_persistent_results(
    monkeypatch,
):
    class SensitiveInput(BaseModel):
        query: str

    async def sensitive_tool(query: str) -> str:
        return query

    def factory():
        return StructuredTool.from_function(
            coroutine=sensitive_tool,
            name="sensitive_tool",
            description="Return a sensitive result",
            args_schema=SensitiveInput,
        )

    runtime = ToolRuntime()
    runtime.catalog.register(
        ToolDescriptor(
            name="sensitive_tool",
            description="Return a sensitive result",
            source=ToolSource.BUILTIN,
            scopes=frozenset({ToolScope.WORKER}),
            capabilities=frozenset({"sensitive.read"}),
            risk=ToolRisk.LOW,
            read_only=True,
            persist_result=False,
            factory=factory,
        )
    )
    toolset = runtime.build_toolset(
        ToolGrantRequest(
            role=ToolScope.WORKER,
            allowed_tools={"sensitive_tool"},
        )
    )
    sensitive_output = "private OCR result: " + ("x" * 800)
    captured_configs = []

    async def fake_execute_many(_calls, config=None):
        captured_configs.append(config)
        return [
            ToolExecutionResult(
                tool_name="sensitive_tool", ok=True, output=sensitive_output
            )
        ]

    toolset.execute_many = fake_execute_many

    class FakeContextManager:
        async def prepare(self, _context, prepared_messages, _budget):
            return type(
                "Transform",
                (),
                {
                    "messages": prepared_messages,
                    "tokens_before": 0,
                    "tokens_after": 0,
                    "actions": [],
                },
            )()

    class FakeLLM:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, _tools):
            return self

        async def ainvoke(self, _messages):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "sensitive_tool",
                            "args": {"query": "inspect"},
                            "id": "call-sensitive",
                        }
                    ],
                )
            return AIMessage(content="done")

    persisted_messages = []
    monkeypatch.setattr(worker_tool, "global_context_manager", FakeContextManager())
    monkeypatch.setattr(worker_tool, "get_tool_llm", lambda: FakeLLM())
    monkeypatch.setattr(
        worker_tool.global_telemetry, "event", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        worker_tool,
        "record_sidechain_transcript",
        lambda _session, _worker, batch: persisted_messages.extend(batch),
    )
    messages = []
    parent = SessionContext(
        session_id="s1",
        user_id="alice",
        workspace_id="nlp",
        channel="web",
        observability_attributes={"unsafe_label": "must-not-propagate"},
    )

    result = await _execute_sandbox_loop(
        "worker-1",
        "s1",
        messages,
        toolset,
        context=parent,
        budget=WorkerResourceBudget(max_tool_result_chars=512),
    )

    assert result.status == "completed"
    assert captured_configs == [
        {
            "configurable": {
                "thread_id": "s1",
                "worker_id": "worker-1",
                "user_id": "alice",
                "workspace_id": "nlp",
                "channel": "worker",
            }
        }
    ]
    in_memory = [item for item in messages if isinstance(item, ToolMessage)]
    persisted = [
        item for item in persisted_messages if isinstance(item, ToolMessage)
    ]
    assert len(in_memory) == 1
    assert in_memory[0].content != sensitive_output
    assert len(str(in_memory[0].content)) <= 512
    assert "[tool result compacted:" in str(in_memory[0].content)
    assert in_memory[0].artifact is None
    assert len(persisted) == 1
    assert (
        persisted[0].content
        == worker_tool.NON_PERSISTED_TOOL_RESULT_PLACEHOLDER
    )
    assert persisted[0].artifact is None
    assert sensitive_output not in str(persisted[0])


@pytest.mark.asyncio
async def test_sandbox_classifies_attempt_timeout_as_recoverable(monkeypatch):
    class SlowLLM:
        async def ainvoke(self, _messages):
            await asyncio.sleep(1)

    monkeypatch.setattr(worker_tool, "get_tool_llm", lambda: SlowLLM())
    result = await _execute_sandbox_loop(
        "slow-worker",
        "s1",
        [],
        [],
        budget=WorkerResourceBudget(max_duration_s=0.01),
    )

    assert result.status == "timed_out"
    assert result.error.category == "timeout"
    assert result.error.retryable is True


@pytest.mark.asyncio
async def test_sandbox_finalizes_usefully_after_turn_budget(monkeypatch):
    class FinalizingLLM:
        async def ainvoke(self, messages):
            if any("RUNTIME_FINALIZATION" in str(item.content) for item in messages):
                return AIMessage(content="partial work summarized")
            return AIMessage(content="", tool_calls=[{
                "name": "missing_tool", "args": {}, "id": "call-1"
            }])

    monkeypatch.setattr(worker_tool, "get_tool_llm", lambda: FinalizingLLM())
    monkeypatch.setattr(worker_tool, "record_sidechain_transcript", lambda *_args: None)
    result = await _execute_sandbox_loop(
        "finalize-worker",
        "s1",
        [],
        [],
        budget=WorkerResourceBudget(max_turns=1),
    )

    assert result.status == "failed"
    assert result.termination_reason == "max_turns"
    assert result.output == "partial work summarized"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        AIMessage(content=""),
        AIMessage(content="partial", response_metadata={"finish_reason": "length"}),
    ],
)
async def test_one_shot_sandbox_never_requeries_or_finalizes(monkeypatch, response):
    class CountingLLM:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, _messages):
            self.calls += 1
            return response

    llm = CountingLLM()
    monkeypatch.setattr(worker_tool, "get_worker_llm", lambda **_kwargs: llm)
    monkeypatch.setattr(worker_tool, "record_sidechain_transcript", lambda *_args: None)
    result = await _execute_sandbox_loop(
        "native-search-worker",
        "s1",
        [],
        [],
        model_name="worker-qwen-web",
        budget=WorkerResourceBudget(
            max_turns=1,
            max_tool_calls=0,
            max_injections=0,
            finalize_on_exhaustion=False,
        ),
    )

    assert llm.calls == 1
    assert result.status == "failed"
    assert result.termination_reason == "max_turns"


@pytest.mark.asyncio
async def test_one_shot_sandbox_makes_one_call_on_a_transient_failure(monkeypatch):
    class FailingLLM:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, _messages):
            self.calls += 1
            raise TimeoutError("provider timeout")

    llm = FailingLLM()
    monkeypatch.setattr(worker_tool, "get_worker_llm", lambda **_kwargs: llm)
    monkeypatch.setattr(worker_tool.global_telemetry, "event", lambda *_args, **_kwargs: None)
    result = await _execute_sandbox_loop(
        "native-search-worker",
        "s1",
        [],
        [],
        model_name="worker-qwen-web",
        budget=WorkerResourceBudget(
            max_turns=1,
            max_tool_calls=0,
            max_injections=0,
            finalize_on_exhaustion=False,
        ),
    )

    assert llm.calls == 1
    assert result.status == "timed_out"
    assert result.error is not None and result.error.retryable is True


@pytest.mark.asyncio
async def test_send_message_rejects_completed_worker_without_restarting():
    result = await worker_tool.send_message.ainvoke(
        {"to_agent_id": "completed-worker", "message": "please continue"},
        config={"configurable": {"thread_id": "s1", "turn_id": "turn-1"}},
    )

    assert json.loads(result)["status"] == "failed"
    assert global_task_manager.get_active_task("completed-worker") is None
