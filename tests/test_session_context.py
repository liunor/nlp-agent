from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from core.session_context import (
    LocalContextStateRepository,
    PersistedContextState,
    SessionContext,
)
from server.agent.compression.auto_compact import autocompact_if_needed
from server.agent.compression.context_collapse import (
    CollapseStore,
    apply_collapses_if_needed,
)
from server.agent.compression.context_manager import ContextManager, trim_legal_history
from utils.tokens import (
    build_context_budget,
    estimate_message_tokens,
    rough_token_count_estimation,
)


def test_session_identity_is_unique_safe_and_collision_resistant():
    first = SessionContext.create(user_id="u1")
    second = SessionContext.create(user_id="u1")
    assert first.session_id != second.session_id
    assert first.storage_key != SessionContext(
        session_id=first.session_id, user_id="u2"
    ).storage_key
    assert first.storage_key != SessionContext(
        session_id=first.session_id, user_id="u1", agent_id="worker-1"
    ).storage_key
    with pytest.raises(ValueError):
        SessionContext(session_id="../escape")
    with pytest.raises(ValueError, match="thread_id"):
        SessionContext.from_config({}, require=True)


def test_context_keeps_auth_session_separate_from_conversation_thread() -> None:
    context = SessionContext.from_config(
        {
            "configurable": {
                "thread_id": "conversation-42",
                "auth_session_id": "login-session-9",
                "user_id": "user-1",
                "workspace_id": "workspace-1",
            }
        },
        require=True,
    )

    assert context.session_id == "conversation-42"
    assert context.auth_session_id == "login-session-9"


def test_context_state_is_persisted_and_isolated_per_session(tmp_path: Path):
    repository = LocalContextStateRepository(tmp_path)
    first = SessionContext(session_id="one", user_id="user")
    second = SessionContext(session_id="two", user_id="user")
    repository.save(
        first,
        PersistedContextState(collapse_commits=[{"collapse_id": "first"}]),
    )
    assert repository.load(first).collapse_commits == [{"collapse_id": "first"}]
    assert repository.load(first).revision == 1
    assert repository.load(second).collapse_commits == []
    assert repository.path_for(first) != repository.path_for(second)


@pytest.mark.asyncio
async def test_context_manager_keeps_staged_spans_per_session(tmp_path: Path):
    manager = ContextManager(LocalContextStateRepository(tmp_path))
    budget = build_context_budget(context_window=50_000, output_reserve=2_000)
    messages = [
        HumanMessage(content=f"turn {index}", id=f"h{index}")
        if index % 2 == 0
        else AIMessage(content=f"reply {index}", id=f"a{index}")
        for index in range(24)
    ]
    first = SessionContext(session_id="first")
    second = SessionContext(session_id="second")
    await manager.prepare(first, messages, budget)
    await manager.prepare(second, messages[:6], budget)
    first_store = manager._stores[first.storage_key]
    second_store = manager._stores[second.storage_key]
    assert first_store is not second_store
    assert len(first_store.staged) > len(second_store.staged)


@pytest.mark.asyncio
async def test_context_manager_isolates_shared_session_id_between_principals_and_workers(
    tmp_path: Path,
):
    repository = LocalContextStateRepository(tmp_path)
    manager = ContextManager(repository)
    budget = build_context_budget(context_window=50_000, output_reserve=2_000)
    messages = [
        HumanMessage(content=f"turn {index}", id=f"h{index}")
        if index % 2 == 0
        else AIMessage(content=f"reply {index}", id=f"a{index}")
        for index in range(24)
    ]
    alice = SessionContext(
        session_id="shared-conversation",
        user_id="alice",
        workspace_id="w1",
        channel="web",
    )
    bob = alice.model_copy(update={"user_id": "bob"})
    alice_worker = alice.model_copy(
        update={"channel": "worker", "agent_id": "worker-1"}
    )

    await manager.prepare(alice, messages, budget)
    await manager.prepare(bob, messages, budget)
    await manager.prepare(alice_worker, messages, budget)

    stores = [manager._stores[context.storage_key] for context in (alice, bob, alice_worker)]
    assert len({id(store) for store in stores}) == 3
    assert len({repository.path_for(context) for context in (alice, bob, alice_worker)}) == 3
    assert all(store.staged for store in stores)


def test_hard_trim_preserves_complete_tool_call_turn():
    messages = [
        SystemMessage(content="policy"),
        HumanMessage(content="old" * 100, id="old-user"),
        AIMessage(content="old reply" * 100, id="old-ai"),
        HumanMessage(content="new request", id="new-user"),
        AIMessage(
            content="",
            id="new-ai",
            tool_calls=[{"name": "lookup", "args": {}, "id": "call-1"}],
        ),
        ToolMessage(
            content="result",
            name="lookup",
            tool_call_id="call-1",
            id="tool-1",
        ),
        AIMessage(content="answer", id="answer"),
    ]
    newest_cost = sum(estimate_message_tokens(item) for item in messages[3:])
    trimmed = trim_legal_history(messages, newest_cost + 20)
    ids = {message.id for message in trimmed}
    assert {"new-user", "new-ai", "tool-1", "answer"}.issubset(ids)
    assert "old-user" not in ids


@pytest.mark.asyncio
async def test_auto_compact_never_leaves_an_orphaned_tool_result(monkeypatch):
    async def summarize(_messages):
        return "older context"

    monkeypatch.setattr(
        "server.agent.compression.auto_compact._generate_global_summary",
        summarize,
    )
    messages = [
        HumanMessage(content="old request", id="old-user"),
        HumanMessage(content="older follow-up", id="older-follow-up"),
        AIMessage(
            content="",
            id="old-tool-call",
            tool_calls=[{"name": "lookup", "args": {}, "id": "call-1"}],
        ),
        ToolMessage(
            content="result",
            name="lookup",
            tool_call_id="call-1",
            id="old-tool-result",
        ),
        *[
            HumanMessage(content=f"recent {index}", id=f"recent-{index}")
            for index in range(9)
        ],
    ]

    compacted = await autocompact_if_needed(messages, threshold=1)

    declared = {
        str(call["id"])
        for message in compacted.messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
        if call.get("id")
    }
    assert all(
        not isinstance(message, ToolMessage)
        or message.tool_call_id in declared
        for message in compacted.messages
    )


@pytest.mark.asyncio
async def test_context_collapse_does_not_commit_when_summary_generation_fails(
    monkeypatch,
):
    class FailingModel:
        async def ainvoke(self, _messages):
            raise ConnectionError("summary model unavailable")

    monkeypatch.setattr(
        "server.agent.llm_factory.get_utility_llm",
        lambda: FailingModel(),
    )
    messages = [
        HumanMessage(content=f"message {index}", id=f"message-{index}")
        for index in range(20)
    ]
    store = CollapseStore()
    await apply_collapses_if_needed(messages, store, input_limit=100_000)

    projected = await apply_collapses_if_needed(messages, store, input_limit=1)

    assert store.commits == []
    assert projected == messages


class Query(BaseModel):
    query: str


async def lookup(query: str) -> str:
    return query


def test_token_budget_counts_cjk_message_overhead_and_tool_schemas():
    assert rough_token_count_estimation("中文测试") >= 4
    tool = StructuredTool.from_function(
        coroutine=lookup,
        name="lookup",
        description="Search a data source",
        args_schema=Query,
    )
    budget = build_context_budget(
        context_window=10_000,
        output_reserve=1_000,
        tools=[tool],
        safety_margin=500,
    )
    assert budget.tool_schema_tokens > 0
    assert budget.input_limit == (
        budget.context_window
        - budget.output_reserve
        - budget.safety_margin
        - budget.tool_schema_tokens
    )
