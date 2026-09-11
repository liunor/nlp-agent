"""Zhipu BigModel GLM Chat Completions adapter."""

from __future__ import annotations

from typing import Any, ClassVar

import httpx
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from typing_extensions import override

from core.model_runtime.adapters.openai_compatible import OpenAICompatibleChatModel
from core.model_runtime.network import model_http_client_kwargs
from core.model_runtime.contracts import (
    ModelDefinition,
    ModelPresetConfig,
    ProviderConfig,
    ReasoningEffort,
)


class GLMChatModel(OpenAICompatibleChatModel):
    """Preserve GLM reasoning and declare provider terminal error reasons."""

    ERROR_FINISH_REASONS: ClassVar[dict[str, str]] = {
        "model_context_window_exceeded": "upstream_context_length_exceeded",
        "network_error": "upstream_overloaded",
        "sensitive": "upstream_unknown",
    }

    @override
    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        payload_messages = payload.get("messages", [])
        for index, message in enumerate(messages):
            if not isinstance(message, AIMessage) or index >= len(payload_messages):
                continue
            reasoning = message.additional_kwargs.get("reasoning_content")
            if reasoning and message.tool_calls:
                payload_messages[index]["reasoning_content"] = reasoning
        return payload


class GLMAdapter:
    """Translate shared presets to GLM-5.3 and GLM-5.2 parameters."""

    @staticmethod
    def _reasoning_effort(model_id: str, preset: ModelPresetConfig) -> str:
        effort = preset.thinking.effort
        if model_id.startswith("glm-5.3"):
            return {
                ReasoningEffort.LOW: "low",
                ReasoningEffort.MEDIUM: "high",
                ReasoningEffort.HIGH: "high",
                ReasoningEffort.MAX: "max",
            }[effort]
        return effort.value

    def build(
        self,
        *,
        provider_name: str,
        provider: ProviderConfig,
        model_name: str,
        model: ModelDefinition,
        preset_name: str,
        preset: ModelPresetConfig,
        api_key: str,
    ) -> GLMChatModel:
        del provider_name, model_name
        if model.model_id.startswith("glm-5.3") and not preset.thinking.enabled:
            raise ValueError(
                f"GLM preset {preset_name!r} cannot disable thinking for "
                f"model {model.model_id!r}"
            )

        timeout = httpx.Timeout(
            preset.timeouts.total_s,
            connect=preset.timeouts.connect_s,
        )
        kwargs: dict[str, Any] = {
            "model": model.model_id,
            "base_url": provider.base_url,
            "api_key": api_key,
            "max_tokens": preset.generation.max_output_tokens,
            "request_timeout": timeout,
            "stream_chunk_timeout": preset.timeouts.stream_idle_s,
            "stream_usage": True,
            "max_retries": 0,
            "default_headers": provider.default_headers or None,
            "reasoning_effort": self._reasoning_effort(model.model_id, preset),
            "extra_body": {
                "thinking": {
                    "type": "enabled" if preset.thinking.enabled else "disabled"
                },
                "tool_stream": True,
            },
        }
        kwargs.update(model_http_client_kwargs(provider.base_url, timeout))
        if preset.generation.temperature is not None:
            kwargs["temperature"] = preset.generation.temperature
        if preset.generation.top_p is not None:
            kwargs["top_p"] = preset.generation.top_p
        return GLMChatModel(**kwargs)
