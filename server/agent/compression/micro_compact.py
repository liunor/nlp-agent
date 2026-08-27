"""按时间衰减清理可重新获取的旧工具结果。"""

import time
from typing import Optional, Set

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from utils.logger import get_logger
from utils.tokens import rough_estimation_for_messages


logger = get_logger("nlp_agent.micro_compact")
GAP_THRESHOLD_MINUTES = 60
KEEP_RECENT = 5
MC_CLEARED_PLACEHOLDER = "[Old tool result content cleared]"
COMPACTABLE_TOOLS: Set[str] = {
    "read_local_file",
    "get_current_time",
    "web_fetch",
}


def register_compactable_tool(tool_name: str) -> None:
    COMPACTABLE_TOOLS.add(tool_name)


def unregister_compactable_tool(tool_name: str) -> None:
    COMPACTABLE_TOOLS.discard(tool_name)


class MicroCompactResult:
    def __init__(
        self,
        messages: list[BaseMessage],
        tokens_freed: int = 0,
        tools_cleared: int = 0,
    ):
        self.messages = messages
        self.tokens_freed = tokens_freed
        self.tools_cleared = tools_cleared


def micro_compact_if_needed(
    messages: list[BaseMessage],
    gap_threshold_minutes: float = GAP_THRESHOLD_MINUTES,
    keep_recent: int = KEEP_RECENT,
    force: bool = False,
) -> MicroCompactResult:
    if not messages:
        return MicroCompactResult(messages)
    if not force:
        gap = _get_gap_since_last_ai_message(messages)
        if gap is None or gap < gap_threshold_minutes:
            return MicroCompactResult(messages)

    compactable_ids = _collect_compactable_tool_ids(messages)
    clear_ids = set(compactable_ids[:-keep_recent]) if keep_recent else set(compactable_ids)
    if not clear_ids:
        return MicroCompactResult(messages)

    updated, tokens_freed, tools_cleared = _apply_micro_compact(messages, clear_ids)
    if tools_cleared:
        logger.info(
            "Micro-Compact completed",
            cleared=tools_cleared,
            tokens_freed=tokens_freed,
        )
    return MicroCompactResult(updated, tokens_freed, tools_cleared)


def _get_gap_since_last_ai_message(messages: list[BaseMessage]) -> Optional[float]:
    now = time.time()
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        metadata = getattr(message, "additional_kwargs", {}) or {}
        response_metadata = getattr(message, "response_metadata", {}) or {}
        timestamp = (
            metadata.get("created_at")
            or metadata.get("timestamp")
            or response_metadata.get("created_at")
            or response_metadata.get("timestamp")
        )
        if timestamp is not None:
            try:
                return (now - float(timestamp)) / 60
            except (TypeError, ValueError):
                pass
    return None


def _collect_compactable_tool_ids(messages: list[BaseMessage]) -> list[str]:
    return [
        message.tool_call_id
        for message in messages
        if isinstance(message, ToolMessage)
        and message.content != MC_CLEARED_PLACEHOLDER
        and (getattr(message, "name", None) or "") in COMPACTABLE_TOOLS
    ]


def _apply_micro_compact(
    messages: list[BaseMessage], clear_ids: Set[str]
) -> tuple[list[BaseMessage], int, int]:
    updated = []
    tokens_freed = 0
    tools_cleared = 0
    for message in messages:
        if not isinstance(message, ToolMessage) or message.tool_call_id not in clear_ids:
            updated.append(message)
            continue
        original_tokens = rough_estimation_for_messages([message])
        tokens_freed += max(0, original_tokens - len(MC_CLEARED_PLACEHOLDER) // 4)
        tools_cleared += 1
        updated.append(
            ToolMessage(
                content=MC_CLEARED_PLACEHOLDER,
                tool_call_id=message.tool_call_id,
                id=message.id,
                name=getattr(message, "name", None),
                additional_kwargs={
                    **getattr(message, "additional_kwargs", {}),
                    "micro_compacted": True,
                },
            )
        )
    return updated, tokens_freed, tools_cleared


def is_micro_compacted(message: BaseMessage) -> bool:
    return isinstance(message, ToolMessage) and bool(
        (getattr(message, "additional_kwargs", {}) or {}).get("micro_compacted")
    )


def get_compactable_tool_names() -> Set[str]:
    return set(COMPACTABLE_TOOLS)

