import time

from langchain_core.messages import AIMessage, ToolMessage

from server.agent.compression.micro_compact import (
    MC_CLEARED_PLACEHOLDER,
    get_compactable_tool_names,
    micro_compact_if_needed,
)


def _tool_message(index: int, name: str = "web_search") -> ToolMessage:
    return ToolMessage(
        content="x" * 1000,
        tool_call_id=f"call-{index}",
        name=name,
    )


def test_only_generic_replayable_tools_are_registered():
    assert get_compactable_tool_names() == {
        "read_local_file",
        "get_current_time",
        "web_search",
        "web_fetch",
    }


def test_force_compaction_keeps_recent_results():
    messages = [_tool_message(index) for index in range(7)]
    result = micro_compact_if_needed(messages, keep_recent=2, force=True)
    assert result.tools_cleared == 5
    assert [message.content for message in result.messages[:5]] == [
        MC_CLEARED_PLACEHOLDER
    ] * 5


def test_recent_conversation_is_not_compacted_without_force():
    messages = [
        AIMessage(content="ok", additional_kwargs={"timestamp": time.time()}),
        _tool_message(1),
    ]
    result = micro_compact_if_needed(messages)
    assert result.tools_cleared == 0

