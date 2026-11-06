import os
import json
from typing import Any, Dict
from langchain_core.messages import ToolMessage
from utils.logger import get_logger

# 触发条件：工具结果字符数超过 50,000
DEFAULT_MAX_RESULT_SIZE_CHARS = 50_000

logger = get_logger("shiliu.tool_persistence")


def persist_tool_messages(
    messages: list[Any],
    config: Dict[str, Any],
    *,
    max_result_chars: int | None = None,
) -> list[Any]:
    """Persist oversized ToolMessages and return the context-safe replacements."""
    session_id = config.get("configurable", {}).get("thread_id", "default_session")
    history_dir = os.path.join(".data", "chat_history", session_id, "tool-results")
    os.makedirs(history_dir, exist_ok=True)
    configured_limit = config.get("configurable", {}).get("max_tool_result_chars")
    if max_result_chars is None:
        max_result_chars = (
            int(configured_limit) if configured_limit else DEFAULT_MAX_RESULT_SIZE_CHARS
        )
    processed_messages = []
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            processed_messages.append(msg)
            continue

        def extract_text(content: Any) -> str:
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "\n".join(
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            return str(content)

        content_str = extract_text(msg.content)
        if len(content_str) <= max_result_chars:
            processed_messages.append(msg)
            continue
        file_path = os.path.join(history_dir, f"{msg.tool_call_id}.txt")
        try:
            with open(file_path, "w", encoding="utf-8") as file:
                file.write(content_str)
        except Exception as error:
            logger.error("Failed to persist tool result", error=str(error))
            processed_messages.append(msg)
            continue
        preview = content_str[:2048]
        msg.content = json.dumps(
            {
                "persisted_output": True,
                "original_size_bytes": len(content_str.encode("utf-8")),
                "file_path": file_path,
                "preview": preview + ("..." if len(content_str) > 2048 else ""),
            },
            ensure_ascii=False,
        )
        processed_messages.append(msg)
    return processed_messages
