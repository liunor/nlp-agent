"""Shared metadata and invocation helpers for non-user-facing context data."""

from __future__ import annotations

import inspect
from collections.abc import Iterable
from typing import Any

from langchain_core.messages import BaseMessage


INTERNAL_CONTEXT_KEY = "internal_context"
COMPRESSION_KIND_KEY = "compression_kind"
COMPRESSION_INTERNAL_METADATA = {
    "compression_internal": True,
    "model_role": "compression",
}
INTERNAL_OUTPUT_MARKERS = (
    "<internal_context",
    "【历史对话摘要】",
    "系统核心记忆压缩报告",
    "<collapsed id=",
    "[Context Snip Boundary]",
    "【自动恢复的上下文】",
)


def internal_context_metadata(kind: str, **extra: Any) -> dict[str, Any]:
    """Return metadata identifying a synthetic, model-only context message."""
    return {
        INTERNAL_CONTEXT_KEY: True,
        COMPRESSION_KIND_KEY: kind,
        **extra,
    }


def is_internal_context_message(message: BaseMessage) -> bool:
    """Return whether a message is synthetic context that must not be public."""
    metadata = getattr(message, "additional_kwargs", {}) or {}
    content = str(getattr(message, "content", "") or "").lstrip()
    # The legacy keys keep older checkpoints private after an application
    # upgrade, even before they have been rewritten with the new marker.
    return bool(
        metadata.get(INTERNAL_CONTEXT_KEY)
        or metadata.get("is_collapsed_summary")
        or metadata.get("snip_marker")
        or metadata.get("snip_boundary")
        or content.startswith("【历史对话摘要】")
        or content.startswith("【自动恢复的上下文】")
        or looks_like_internal_output(content)
    )


def public_transcript_messages(messages: Iterable[BaseMessage]) -> list[BaseMessage]:
    """Filter synthetic context messages from the user-facing transcript."""
    return [message for message in messages if not is_internal_context_message(message)]


def looks_like_internal_output(content: Any) -> bool:
    """Detect a model response that is actually a synthetic context payload."""
    text = str(content or "").lstrip()
    return any(marker in text[:512] for marker in INTERNAL_OUTPUT_MARKERS)


def sanitize_public_output(content: Any) -> str:
    """Never return a synthetic context payload as a user-facing answer."""
    text = str(content or "")
    return "" if looks_like_internal_output(text) else text


async def invoke_internal_model(model: Any, messages: list[BaseMessage]) -> Any:
    """Invoke a compaction model with metadata that prevents public streaming.

    Small test doubles and legacy adapters do not always accept LangChain's
    optional ``config`` parameter, so the capability is detected from the
    public ``ainvoke`` signature instead of catching model errors broadly.
    """
    invoke = model.ainvoke
    try:
        parameters = inspect.signature(invoke).parameters
    except (TypeError, ValueError):
        parameters = {}
    accepts_config = not parameters or "config" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if accepts_config:
        return await invoke(
            messages,
            config={"metadata": dict(COMPRESSION_INTERNAL_METADATA)},
        )
    return await invoke(messages)
