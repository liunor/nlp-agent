"""Moonshot Kimi Chat Completions adapter."""

from __future__ import annotations

from typing import Any

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
)


class KimiChatModel(OpenAICompatibleChatModel):
    """Preserve Kimi reasoning while keeping plain-turn prefixes stable."""

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


class KimiAdapter:
    """Translate shared presets to Kimi K2.6 API parameters."""

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
    ) -> KimiChatModel:
        del provider_name, model_name
        forbidden_sampling = [
            name
            for name, value in (
                ("temperature", preset.generation.temperature),
                ("top_p", preset.generation.top_p),
            )
            if value is not None
        ]
        if forbidden_sampling:
            fields = ", ".join(forbidden_sampling)
            raise ValueError(
                f"Kimi preset {preset_name!r} must omit sampling parameters: {fields}"
            )

        timeout = httpx.Timeout(
            preset.timeouts.total_s,
            connect=preset.timeouts.connect_s,
        )
        thinking = {
            "type": "enabled" if preset.thinking.enabled else "disabled",
            "keep": None,
        }
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
            "extra_body": {"thinking": thinking},
        }
        kwargs.update(model_http_client_kwargs(provider.base_url, timeout))
        return KimiChatModel(**kwargs)
