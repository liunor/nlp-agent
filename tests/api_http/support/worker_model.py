"""Deterministic LangChain chat model used by the isolated Phase 5 Worker."""

from __future__ import annotations

import time
from typing import Any, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr


class DeterministicWorkerChatModel(BaseChatModel):
    """A local model seam with explicit success, delay, and failure markers."""

    model_name: str = "api-http-deterministic-worker"
    context_window_tokens: int = 128_000
    max_output_tokens: int = 2_048
    _call_count: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "api_http_deterministic_worker"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> "DeterministicWorkerChatModel":
        del tools, tool_choice, kwargs
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        self._call_count += 1
        content = ""
        for message in reversed(messages):
            if getattr(message, "type", None) == "human":
                content = str(getattr(message, "content", ""))
                break
        if "__api_http_timeout__" in content:
            raise TimeoutError("deterministic API HTTP Worker timeout")
        if "__api_http_failure__" in content:
            raise RuntimeError("deterministic API HTTP Worker failure")
        if "__api_http_slow__" in content:
            time.sleep(0.75)
        response = AIMessage(
            content=f"deterministic-worker:{content}",
            response_metadata={"finish_reason": "stop"},
        )
        return ChatResult(generations=[ChatGeneration(message=response)])


def install_deterministic_worker_model() -> DeterministicWorkerChatModel:
    """Patch only the Worker process' planner factory before graph import."""

    import server.agent.llm_factory as llm_factory

    model = DeterministicWorkerChatModel()
    llm_factory.get_planner_llm = lambda _model_profile=None: model  # type: ignore[assignment]
    return model
