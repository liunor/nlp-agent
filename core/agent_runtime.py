"""Shared runtime resilience contracts for Coordinator and Worker agents."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from enum import Enum
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import BaseModel, ConfigDict, Field


class AgentStopReason(str, Enum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    MODEL_TIMEOUT = "model_timeout"
    MODEL_ERROR = "model_error"
    TOOL_ERROR = "tool_error"
    TOKEN_BUDGET = "token_budget"
    TOOL_BUDGET = "tool_budget"
    MAX_ITERATIONS = "max_iterations"
    MAX_DURATION = "max_duration"
    EMPTY_RESPONSE = "empty_response"


class AgentRunBudget(BaseModel):
    """Validated limits applied to one logical Agent turn."""

    model_config = ConfigDict(frozen=True)

    max_iterations: int = Field(default=12, ge=1, le=200)
    max_duration_s: float = Field(default=600.0, gt=0, le=7200)
    max_tokens: int = Field(default=128_000, ge=1)
    max_tool_calls: int = Field(default=32, ge=0, le=1000)
    max_injections: int = Field(default=15, ge=0, le=100)
    injection_batch_size: int = Field(default=3, ge=1, le=20)
    max_tool_result_chars: int = Field(default=50_000, ge=512, le=2_000_000)
    finalize_on_exhaustion: bool = True


class AgentRunSnapshot(BaseModel):
    """Serializable counters carried through a graph or sandbox loop."""

    turn_id: str = ""
    started_at: float = Field(default_factory=time.time)
    iterations: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    injections: int = Field(default=0, ge=0)
    stop_reason: AgentStopReason | None = None
    finalizing: bool = False

    def elapsed_s(self) -> float:
        return max(0.0, time.time() - self.started_at)

    def limit_reached(self, budget: AgentRunBudget) -> AgentStopReason | None:
        if self.elapsed_s() >= budget.max_duration_s:
            return AgentStopReason.MAX_DURATION
        if self.iterations >= budget.max_iterations:
            return AgentStopReason.MAX_ITERATIONS
        if self.tokens >= budget.max_tokens:
            return AgentStopReason.TOKEN_BUDGET
        if self.tool_calls > 0 and self.tool_calls >= budget.max_tool_calls:
            return AgentStopReason.TOOL_BUDGET
        return None

    def can_reserve_tools(self, count: int, budget: AgentRunBudget) -> bool:
        return count >= 0 and self.tool_calls + count <= budget.max_tool_calls


class AgentInjectionBus:
    """Session-scoped inbox drained only at safe Agent iteration boundaries."""

    def __init__(self) -> None:
        self._queues: dict[str, deque[BaseMessage]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def publish(self, session_id: str, message: BaseMessage | str) -> None:
        normalized = message if isinstance(message, BaseMessage) else HumanMessage(content=message)
        async with self._lock:
            self._queues[session_id].append(normalized)

    async def drain(
        self,
        session_id: str,
        *,
        limit: int,
        remaining_total: int | None = None,
    ) -> list[BaseMessage]:
        if limit <= 0 or remaining_total == 0:
            return []
        effective_limit = min(limit, remaining_total) if remaining_total is not None else limit
        async with self._lock:
            queue = self._queues.get(session_id)
            if not queue:
                return []
            drained = [queue.popleft() for _ in range(min(effective_limit, len(queue)))]
            if not queue:
                self._queues.pop(session_id, None)
            return drained

    async def clear(self, session_id: str) -> None:
        async with self._lock:
            self._queues.pop(session_id, None)

    def pending(self, session_id: str) -> int:
        return len(self._queues.get(session_id, ()))


def usage_total(message: Any) -> int:
    metadata = getattr(message, "usage_metadata", None) or {}
    return int(metadata.get("total_tokens") or (
        int(metadata.get("input_tokens", 0)) + int(metadata.get("output_tokens", 0))
    ))


def compact_model_content(content: str, max_chars: int) -> str:
    """Bound model-visible content while retaining useful head and tail context."""

    if len(content) <= max_chars:
        return content
    marker = (
        f"\n\n[tool result compacted: original_chars={len(content)}, "
        f"visible_chars={max_chars}]\n\n"
    )
    available = max(0, max_chars - len(marker))
    head = max(1, available * 3 // 4)
    tail = max(0, available - head)
    return content[:head] + marker + (content[-tail:] if tail else "")


def exhaustion_prompt(reason: AgentStopReason) -> str:
    from core.prompt_runtime import global_prompt_runtime

    return global_prompt_runtime.render("runtime.exhaustion", reason=reason.value)


def exhaustion_fallback(reason: AgentStopReason) -> str:
    return (
        f"本轮执行因 {reason.value} 达到运行限制而停止。"
        "模型未能生成最终总结；已有工具结果和中间状态已保留，可在下一条消息中继续。"
    )


def configured_budget(role: str, **overrides: Any) -> AgentRunBudget:
    """Load one role budget while keeping validation in this module."""

    from configs.settings import settings

    values = dict(settings.get_agent_runtime(role))
    if role == "coordinator":
        from core.vision_execution import current_image_turn

        image_turn = current_image_turn()
        if image_turn is not None:
            values["max_duration_s"] = image_turn.timeout_s
    values.update({key: value for key, value in overrides.items() if value is not None})
    return AgentRunBudget.model_validate(values)


global_agent_injections = AgentInjectionBus()
