from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import server.agent.compression.auto_compact as auto_compact_module
import server.agent.compression.context_collapse as context_collapse_module
from core.session_context import LocalContextStateRepository, SessionContext
from server.agent.session_storage import SessionStorageManager, load_transcript_file
from server.agent.compression.context_manager import ContextManager, trim_legal_history
from server.agent.compression.internal_context import (
    internal_context_metadata,
    is_internal_context_message,
    looks_like_internal_output,
    public_transcript_messages,
    sanitize_public_output,
)
from utils.tokens import ContextBudget, rough_estimation_for_messages


def _budget(input_limit: int) -> ContextBudget:
    return ContextBudget(
        context_window=input_limit,
        output_reserve=0,
        safety_margin=0,
    )


def _history_messages(*, human_count: int, human_chars: int) -> list:
    messages = [SystemMessage(content="coordinator policy", id="system")]
    for index in range(human_count):
        messages.append(
            HumanMessage(
                content=f"history-{index} " + ("context " * (human_chars // 8)),
                id=f"human-{index}",
            )
        )
    return messages


@pytest.fixture
def context_root():
    root = Path(".test-artifacts") / f"context-compression-{uuid.uuid4().hex}"
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.asyncio
async def test_micro_compact_is_the_only_automatic_layer_for_one_prepare(
    context_root, monkeypatch
):
    summary_calls = 0

    async def summarize(_messages):
        nonlocal summary_calls
        summary_calls += 1
        return "internal summary"

    monkeypatch.setattr(auto_compact_module, "_generate_global_summary", summarize)
    monkeypatch.setattr(auto_compact_module, "_consecutive_failures", {})
    monkeypatch.setattr(
        "server.memory.runtime.global_memory_runtime.archive_summary",
        lambda *_args, **_kwargs: None,
    )

    messages = _history_messages(human_count=62, human_chars=1_300)
    messages.extend(
        ToolMessage(
            content="tool result " * 330,
            tool_call_id=f"tool-{index}",
            name="web_fetch",
            id=f"tool-message-{index}",
        )
        for index in range(7)
    )

    transform = await ContextManager(
        LocalContextStateRepository(context_root)
    ).prepare(
        SessionContext(session_id="micro-only"),
        messages,
        _budget(33_000),
    )

    assert transform.actions == ["micro_compact:2"]
    assert summary_calls == 0


@pytest.mark.asyncio
async def test_collapse_commit_is_not_followed_by_auto_compact_in_same_prepare(
    context_root, monkeypatch
):
    auto_summary_calls = 0

    async def summarize_span(_messages):
        return "collapse summary"

    async def summarize_all(_messages):
        nonlocal auto_summary_calls
        auto_summary_calls += 1
        return "auto summary"

    monkeypatch.setattr(
        context_collapse_module, "_generate_span_summary", summarize_span
    )
    monkeypatch.setattr(auto_compact_module, "_generate_global_summary", summarize_all)
    monkeypatch.setattr(auto_compact_module, "_consecutive_failures", {})
    monkeypatch.setattr(
        "server.memory.runtime.global_memory_runtime.archive_summary",
        lambda *_args, **_kwargs: None,
    )

    manager = ContextManager(LocalContextStateRepository(context_root))
    context = SessionContext(session_id="collapse-only")
    initial = _history_messages(human_count=25, human_chars=1_000)
    await manager.prepare(context, initial, _budget(10_000))

    expanded = [
        *initial,
        *[
            HumanMessage(
                content=f"new-history-{index} " + ("context " * 500),
                id=f"new-human-{index}",
            )
            for index in range(20)
        ],
    ]
    transform = await manager.prepare(context, expanded, _budget(10_000))

    assert any(action.startswith("collapse:") for action in transform.actions)
    assert "auto_compact" not in transform.actions
    assert auto_summary_calls == 0


@pytest.mark.asyncio
async def test_auto_compact_is_idempotent_and_replaces_its_previous_summary(monkeypatch):
    summary_calls = 0

    async def summarize(_messages):
        nonlocal summary_calls
        summary_calls += 1
        return f"summary-{summary_calls}"

    monkeypatch.setattr(auto_compact_module, "_generate_global_summary", summarize)
    monkeypatch.setattr(auto_compact_module, "_consecutive_failures", {})

    messages = [
        HumanMessage(content=f"old message {index} " + ("context " * 80), id=f"old-{index}")
        for index in range(13)
    ]

    first = await auto_compact_module.autocompact_if_needed(messages, threshold=1)
    assert first.was_compacted is True
    assert summary_calls == 1
    first_summary = next(
        message
        for message in first.messages
        if (getattr(message, "additional_kwargs", {}) or {}).get(
            "compression_kind"
        )
        == "auto_compact"
    )
    assert is_internal_context_message(first_summary)

    repeated = await auto_compact_module.autocompact_if_needed(
        first.messages, threshold=1
    )
    assert repeated.was_compacted is False
    assert repeated.messages == first.messages
    assert summary_calls == 1

    continued = [
        *first.messages,
        HumanMessage(content="new user request " + ("new context " * 80), id="new-1"),
        HumanMessage(content="new follow-up " + ("follow-up context " * 80), id="new-2"),
    ]
    second = await auto_compact_module.autocompact_if_needed(continued, threshold=1)
    assert second.was_compacted is True
    assert summary_calls == 2
    summaries = [
        message
        for message in second.messages
        if (getattr(message, "additional_kwargs", {}) or {}).get(
            "compression_kind"
        )
        == "auto_compact"
    ]
    assert len(summaries) == 1
    assert "summary-2" in summaries[0].content


@pytest.mark.asyncio
async def test_auto_compact_restoration_is_internal_context(monkeypatch):
    async def summarize(_messages):
        return "facts"

    monkeypatch.setattr(auto_compact_module, "_generate_global_summary", summarize)
    monkeypatch.setattr(auto_compact_module, "_consecutive_failures", {})
    messages = [
        AIMessage(
            content="read file",
            id="old-tool-call",
            tool_calls=[
                {
                    "name": "read_local_file",
                    "args": {"file_path": "E:/workspace/main.py"},
                    "id": "tool-call-1",
                }
            ],
        ),
        *[
            HumanMessage(content=f"history {index} " + ("context " * 100), id=f"history-{index}")
            for index in range(12)
        ],
    ]

    result = await auto_compact_module.autocompact_if_needed(messages, threshold=1)

    restoration = next(
        message
        for message in result.messages
        if (getattr(message, "additional_kwargs", {}) or {}).get(
            "compression_kind"
        )
        == "restoration"
    )
    assert is_internal_context_message(restoration)
    assert "E:/workspace/main.py" in restoration.content


def test_public_transcript_excludes_every_synthetic_compression_message():
    internal_summary = SystemMessage(
        content="<internal_context>summary</internal_context>",
        id="summary",
        additional_kwargs=internal_context_metadata("auto_compact"),
    )
    legacy_collapse = SystemMessage(
        content='<collapsed id="c1">old facts</collapsed>',
        id="collapse",
        additional_kwargs={"is_collapsed_summary": True},
    )
    leaked_report = AIMessage(
        content="系统核心记忆压缩报告\n旧内容已被压缩。",
        id="leaked-report",
    )
    messages = [
        HumanMessage(content="question", id="question"),
        internal_summary,
        legacy_collapse,
        leaked_report,
        HumanMessage(content="follow up", id="follow-up"),
    ]

    assert public_transcript_messages(messages) == [messages[0], messages[4]]


def test_legacy_jsonl_transcript_queue_also_excludes_internal_messages(monkeypatch):
    manager = SessionStorageManager()
    monkeypatch.setattr(manager, "ensure_drain_task", lambda: None)
    internal_summary = SystemMessage(
        content="internal",
        id="internal-summary",
        additional_kwargs=internal_context_metadata("auto_compact"),
    )

    manager.enqueue_messages(
        "transcript-session",
        [
            HumanMessage(content="question", id="question"),
            internal_summary,
            HumanMessage(content="answer context", id="answer"),
        ],
    )

    queued = manager._write_queues["transcript-session"]
    assert [entry.uuid for entry in queued] == ["question", "answer"]


@pytest.mark.asyncio
async def test_legacy_jsonl_replay_excludes_old_compression_reports(
    context_root, monkeypatch
):
    transcript = context_root / "legacy.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    entries = [
        {
            "uuid": "question",
            "parentUuid": None,
            "sessionId": "legacy-session",
            "timestamp": 1,
            "type": "user",
            "role": "user",
            "content": "question",
        },
        {
            "uuid": "compression-report",
            "parentUuid": "question",
            "sessionId": "legacy-session",
            "timestamp": 2,
            "type": "assistant",
            "role": "assistant",
            "content": "系统核心记忆压缩报告\n旧上下文",
        },
        {
            "uuid": "follow-up",
            "parentUuid": "compression-report",
            "sessionId": "legacy-session",
            "timestamp": 3,
            "type": "user",
            "role": "user",
            "content": "follow up",
        },
    ]
    transcript.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "server.agent.session_storage.get_session_transcript_path",
        lambda _session_id: str(transcript),
    )

    replayed = await load_transcript_file("legacy-session")

    assert [message.id for message in replayed] == ["question", "follow-up"]


@pytest.mark.asyncio
async def test_internal_model_invocation_carries_non_public_event_metadata():
    captured = {}

    class Model:
        async def ainvoke(self, messages, config=None):
            captured["messages"] = messages
            captured["config"] = config
            return HumanMessage(content="summary")

    from server.agent.compression.internal_context import invoke_internal_model

    await invoke_internal_model(Model(), [HumanMessage(content="history")])

    assert captured["config"]["metadata"] == {
        "compression_internal": True,
        "model_role": "compression",
    }


@pytest.mark.asyncio
async def test_coordinator_writes_in_place_compaction_replacements_back_to_state(
    monkeypatch,
):
    import server.agent.compression.context_manager as context_manager_module
    import server.agent.node.coordinator as coordinator_module

    original = ToolMessage(
        content="large tool result",
        tool_call_id="tool-call",
        name="web_fetch",
        id="tool-result",
    )
    replacement = ToolMessage(
        content="[Old tool result content cleared]",
        tool_call_id="tool-call",
        name="web_fetch",
        id="tool-result",
        additional_kwargs={"micro_compacted": True},
    )
    captured = {}

    class Planner:
        context_window_tokens = 100_000
        max_output_tokens = 8_000

    async def prepare(_session, _messages, _budget):
        from server.agent.compression.context_manager import ContextTransform

        return ContextTransform(
            session=SessionContext(session_id="coordinator-state"),
            messages=[SystemMessage(content="policy", id="policy"), replacement],
            tokens_before=1_000,
            tokens_after=500,
            actions=["micro_compact:1"],
        )

    async def invoke(_model, messages, _config, *, name):
        captured["messages"] = messages
        captured["name"] = name
        return AIMessage(content="answer")

    async def no_injections(*_args, **_kwargs):
        return []

    monkeypatch.setattr(
        coordinator_module, "_get_system_message", lambda: SystemMessage(content="policy", id="policy")
    )
    monkeypatch.setattr(
        coordinator_module,
        "get_coordinator_toolset",
        lambda _config: type("ToolSet", (), {"tools": []})(),
    )
    monkeypatch.setattr(coordinator_module, "_get_llm_with_tools", lambda _config: Planner())
    monkeypatch.setattr(coordinator_module, "invoke_model_with_telemetry", invoke)
    monkeypatch.setattr(coordinator_module.global_memory_runtime, "context_message", lambda _session: None)
    monkeypatch.setattr(coordinator_module.global_agent_injections, "drain", no_injections)
    monkeypatch.setattr(context_manager_module.global_context_manager, "prepare", prepare)

    result = await coordinator_module.coordinator_node(
        {"messages": [original]},
        {"configurable": {"thread_id": "coordinator-state", "turn_id": "turn-1"}},
    )

    assert captured["name"] == "coordinator.model"
    assert replacement in result["messages"]
    assert any(message.id == "tool-result" for message in captured["messages"])


def test_public_output_sanitizer_drops_internal_context_reports_only():
    assert looks_like_internal_output("系统核心记忆压缩报告\n...") is True
    assert sanitize_public_output("系统核心记忆压缩报告\n...") == ""
    assert sanitize_public_output("这是正常的用户回答。") == "这是正常的用户回答。"


@pytest.mark.asyncio
async def test_auto_compact_requires_the_active_model_threshold():
    with pytest.raises(ValueError, match="explicit threshold"):
        await auto_compact_module.autocompact_if_needed(
            [HumanMessage(content="history", id="history")]
        )


def test_runtime_budget_is_not_capped_by_the_legacy_167k_default():
    deepseek = _budget(1_000_000)
    qwen = _budget(32_000)

    assert deepseek.input_limit == 1_000_000
    assert deepseek.threshold(0.93) == 930_000
    assert qwen.input_limit == 32_000
    assert qwen.threshold(0.93) == 29_760


def test_hard_trim_does_not_treat_large_internal_summaries_as_unremovable():
    from server.agent.compression.context_manager import trim_legal_history

    large_summary = SystemMessage(
        content="summary " * 2_000,
        id="large-summary",
        additional_kwargs=internal_context_metadata("auto_compact"),
    )
    messages = [
        SystemMessage(content="base policy", id="policy"),
        large_summary,
        HumanMessage(content="old " * 500, id="old"),
        HumanMessage(content="latest request", id="latest"),
    ]

    trimmed = trim_legal_history(messages, token_limit=300)

    assert all(message.id != "large-summary" for message in trimmed)
    assert any(message.id == "latest" for message in trimmed)


def test_hard_trim_never_returns_an_oversized_system_prompt():
    messages = [
        SystemMessage(content="policy " * 2_000, id="oversized-policy"),
        SystemMessage(content="small policy", id="small-policy"),
        HumanMessage(content="latest request", id="latest"),
    ]

    trimmed = trim_legal_history(messages, token_limit=100)

    assert rough_estimation_for_messages(trimmed) <= 100
    assert all(message.id != "oversized-policy" for message in trimmed)


@pytest.mark.asyncio
async def test_worker_reuses_the_compacted_view_on_the_next_react_iteration(monkeypatch):
    import server.tools.worker_tool as worker_tool_module
    from core.tool_runtime import ToolSet
    from core.worker_lifecycle import WorkerResourceBudget
    from server.agent.compression.context_manager import ContextTransform

    raw_tool = ToolMessage(
        content="raw tool result",
        tool_call_id="call-0",
        name="web_fetch",
        id="tool-0",
    )
    compacted_tool = ToolMessage(
        content="[Old tool result content cleared]",
        tool_call_id="call-0",
        name="web_fetch",
        id="tool-0",
        additional_kwargs={"micro_compacted": True},
    )
    prepare_inputs = []

    class FakeContextManager:
        async def prepare(self, context, incoming, _budget):
            prepare_inputs.append(list(incoming))
            if len(prepare_inputs) == 1:
                return ContextTransform(
                    session=context,
                    messages=[compacted_tool],
                    tokens_before=100,
                    tokens_after=20,
                    actions=["micro_compact:1"],
                )
            return ContextTransform(
                session=context,
                messages=list(incoming),
                tokens_before=100,
                tokens_after=100,
            )

    class FakeToolSet(ToolSet):
        def __init__(self):
            pass

        @property
        def tools(self):
            return []

        def descriptor(self, _name):
            return None

        async def execute_many(self, _calls, _config):
            class Execution:
                ok = True

                def to_model_content(self, *, max_chars):
                    del max_chars
                    return "tool result"

                def model_dump(self, *, mode):
                    del mode
                    return {}

            return [
                Execution()
            ]

    class FakeLLM:
        context_window_tokens = 100_000
        max_output_tokens = 8_000

        def __init__(self):
            self.calls = []

        async def ainvoke(self, incoming):
            self.calls.append(list(incoming))
            if len(self.calls) == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "lookup", "args": {}, "id": "call-1"}
                    ],
                )
            return AIMessage(content="done")

    llm = FakeLLM()
    monkeypatch.setattr(worker_tool_module, "global_context_manager", FakeContextManager())
    monkeypatch.setattr(worker_tool_module, "get_tool_llm", lambda: llm)
    monkeypatch.setattr(worker_tool_module, "record_sidechain_transcript", lambda *_args: None)
    monkeypatch.setattr(worker_tool_module.global_task_manager, "get_active_task", lambda _worker_id: None)

    result = await worker_tool_module._execute_sandbox_loop(
        "worker-1",
        "session-1",
        [raw_tool],
        FakeToolSet(),
        "",
        budget=WorkerResourceBudget(
            max_turns=2,
            max_tool_calls=1,
            max_tokens=1_000,
            max_tool_result_chars=512,
        ),
        context=SessionContext(session_id="session-1"),
    )

    assert result.status == "completed"
    assert len(prepare_inputs) == 2
    assert compacted_tool in prepare_inputs[1]
    assert raw_tool not in prepare_inputs[1]


@pytest.mark.asyncio
async def test_long_context_compression_simulation_covers_all_runtime_layers(
    context_root, monkeypatch
):
    from server.agent.compression.micro_compact import MC_CLEARED_PLACEHOLDER
    from server.agent.compression.snip_compact import (
        is_snip_boundary_message,
        is_snip_marker_message,
        snip_compact_if_needed,
    )
    from server.agent.compression.tool_persistence import persist_tool_messages

    span_calls = 0
    auto_calls = 0

    async def summarize_span(_messages):
        nonlocal span_calls
        span_calls += 1
        return "stable collapse facts"

    async def summarize_all(_messages):
        nonlocal auto_calls
        auto_calls += 1
        return f"stable auto facts {auto_calls}"

    monkeypatch.setattr(
        context_collapse_module, "_generate_span_summary", summarize_span
    )
    monkeypatch.setattr(auto_compact_module, "_generate_global_summary", summarize_all)
    monkeypatch.setattr(auto_compact_module, "_consecutive_failures", {})
    monkeypatch.setattr(
        "server.memory.runtime.global_memory_runtime.archive_summary",
        lambda *_args, **_kwargs: None,
    )

    # Layer 1: an oversized tool result becomes a small replayable preview.
    raw_tool = ToolMessage(
        content="remote payload " * 200,
        tool_call_id="persisted-call",
        name="web_fetch",
        id="persisted-tool",
    )
    raw_tool_chars = len(raw_tool.content)
    persisted = persist_tool_messages(
        [raw_tool],
        {"configurable": {"thread_id": "long-layer-simulation"}},
        max_result_chars=100,
    )
    persisted_payload = json.loads(persisted[0].content)
    assert persisted_payload["persisted_output"] is True
    assert persisted_payload["original_size_bytes"] > 100
    assert len(persisted[0].content) < raw_tool_chars

    # Layer 2: Micro Compact changes only old eligible tool results and stops
    # the pipeline before any summarizing layer is entered.
    micro_messages = _history_messages(human_count=62, human_chars=1_300)
    for index in range(7):
        call_id = f"micro-call-{index}"
        micro_messages.extend(
            [
                AIMessage(
                    content="",
                    id=f"micro-ai-{index}",
                    tool_calls=[
                        {
                            "name": "web_fetch",
                            "args": {},
                            "id": call_id,
                        }
                    ],
                ),
                ToolMessage(
                    content="old tool result " * 330,
                    tool_call_id=call_id,
                    name="web_fetch",
                    id=f"micro-tool-{index}",
                ),
            ]
        )
    micro_transform = await ContextManager(
        LocalContextStateRepository(context_root / "micro")
    ).prepare(
        SessionContext(session_id="long-micro"),
        micro_messages,
        _budget(36_000),
    )
    assert micro_transform.actions[0].startswith("micro_compact:")
    assert "auto_compact" not in micro_transform.actions
    assert span_calls == 0
    assert any(
        isinstance(message, ToolMessage)
        and message.content == MC_CLEARED_PLACEHOLDER
        for message in micro_transform.messages
    )

    # Layer 3: Snip remains an explicit, zero-LLM operation and marks its
    # synthetic boundary/markers as internal context.
    snip_messages = _history_messages(human_count=30, human_chars=900)
    snip_result = snip_compact_if_needed(
        snip_messages,
        threshold=1_000,
        target_free=500,
        keep_recent=10,
    )
    assert snip_result.tokens_freed > 0
    assert any(is_snip_boundary_message(message) for message in snip_result.messages)
    assert any(is_snip_marker_message(message) for message in snip_result.messages)
    assert all(
        is_internal_context_message(message)
        for message in snip_result.messages
        if is_snip_boundary_message(message) or is_snip_marker_message(message)
    )

    # Layers 4 and 5: first stage below the collapse threshold, then commit on
    # the next high-pressure call. Auto Compact is deliberately deferred to a
    # later prepare call instead of being chained into the same one.
    manager = ContextManager(LocalContextStateRepository(context_root / "collapse"))
    context = SessionContext(session_id="long-collapse")
    initial = _history_messages(human_count=25, human_chars=1_000)
    staged = await manager.prepare(context, initial, _budget(10_000))
    assert staged.actions == []
    assert len(manager._stores[context.storage_key].staged) > 0

    expanded = [
        *initial,
        *[
            HumanMessage(
                content=f"new history {index} " + ("context " * 500),
                id=f"expanded-{index}",
            )
            for index in range(24)
        ],
    ]
    collapsed = await manager.prepare(context, expanded, _budget(36_000))
    assert any(action.startswith("collapse:") for action in collapsed.actions)
    assert "auto_compact" not in collapsed.actions
    assert collapsed.removed_message_ids
    collapse_summaries = [
        message
        for message in collapsed.messages
        if (getattr(message, "additional_kwargs", {}) or {}).get(
            "compression_kind"
        )
        == "collapse"
    ]
    assert len(collapse_summaries) == 1
    assert is_internal_context_message(collapse_summaries[0])

    auto_compacted = await manager.prepare(context, collapsed.messages, _budget(36_000))
    assert "auto_compact" in auto_compacted.actions
    assert auto_calls == 1
    auto_summaries = [
        message
        for message in auto_compacted.messages
        if (getattr(message, "additional_kwargs", {}) or {}).get(
            "compression_kind"
        )
        == "auto_compact"
    ]
    assert len(auto_summaries) == 1
    assert is_internal_context_message(auto_summaries[0])
    assert auto_compacted.tokens_after <= auto_compacted.tokens_before

    # Replaying the exact post-compaction view is a no-op; the watermark is
    # what prevents a later question from regenerating the same summary.
    replayed = await manager.prepare(context, auto_compacted.messages, _budget(36_000))
    assert "auto_compact" not in replayed.actions
    assert auto_calls == 1
