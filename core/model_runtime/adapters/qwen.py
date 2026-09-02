"""Alibaba Cloud Qwen Chat Completions adapter."""

from __future__ import annotations

from typing import Any

import httpx
import openai
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    InputTokenDetails,
    OutputTokenDetails,
    UsageMetadata,
)
from langchain_openai import ChatOpenAI
from typing_extensions import override

from core.model_runtime.contracts import (
    ModelDefinition,
    ModelPresetConfig,
    ProviderConfig,
    ReasoningEffort,
)
from core.model_runtime.normalization import normalize_usage


class QwenChatModel(ChatOpenAI):
    """Preserve Qwen reasoning deltas and normalized provider usage."""

    @staticmethod
    def _usage_metadata(usage: dict[str, Any]) -> UsageMetadata:
        normalized = normalize_usage(usage)
        return UsageMetadata(
            input_tokens=normalized["input_tokens"],
            output_tokens=normalized["output_tokens"],
            total_tokens=normalized["total_tokens"],
            input_token_details=InputTokenDetails(
                **normalized["input_token_details"]
            ),
            output_token_details=OutputTokenDetails(
                **normalized["output_token_details"]
            ),
        )

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
            if reasoning:
                payload_messages[index]["reasoning_content"] = reasoning
        return payload

    @override
    def _create_chat_result(
        self,
        response: dict[str, Any] | openai.BaseModel,
        generation_info: dict[str, Any] | None = None,
    ):
        result = super()._create_chat_result(response, generation_info)
        raw = response if isinstance(response, dict) else response.model_dump()
        response_id = raw.get("id")
        raw_usage = raw.get("usage") or {}
        usage = normalize_usage(raw_usage)
        choices = raw.get("choices") or []
        for index, generation in enumerate(result.generations):
            if response_id:
                generation.message.response_metadata["provider_response_id"] = response_id
                generation.message.additional_kwargs["provider_response_id"] = response_id
            if index < len(choices):
                reasoning = (choices[index].get("message") or {}).get("reasoning_content")
                if reasoning:
                    generation.message.additional_kwargs["reasoning_content"] = reasoning
            generation.message.additional_kwargs["provider_usage"] = usage
            generation.message.additional_kwargs["provider_usage_raw"] = raw_usage
            if usage["total_tokens"] and isinstance(generation.message, AIMessage):
                generation.message.usage_metadata = self._usage_metadata(raw.get("usage") or {})
        return result

    @override
    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict[str, Any],
        default_chunk_class: type,
        base_generation_info: dict[str, Any] | None,
    ):
        result = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if result is None:
            return None
        response_id = chunk.get("id") or chunk.get("chunk", {}).get("id")
        if response_id:
            result.message.response_metadata["provider_response_id"] = response_id
            result.message.additional_kwargs["provider_response_id"] = response_id
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
        if choices:
            reasoning = (choices[0].get("delta") or {}).get("reasoning_content")
            if reasoning:
                result.message.additional_kwargs["reasoning_content"] = reasoning
        if chunk.get("usage"):
            raw_usage = chunk["usage"]
            usage = normalize_usage(raw_usage, default_semantics="cumulative")
            result.message.additional_kwargs["provider_usage"] = usage
            result.message.additional_kwargs["provider_usage_raw"] = raw_usage
            result.message.additional_kwargs["provider_usage_semantics"] = usage["usage_semantics"]
            if isinstance(result.message, AIMessageChunk):
                result.message.usage_metadata = self._usage_metadata(chunk["usage"])
        return result


class QwenAdapter:
    """Translate shared presets to Qwen-specific compatible API parameters."""

    @staticmethod
    def _extra_body(model_id: str, preset: ModelPresetConfig) -> dict[str, Any]:
        body: dict[str, Any] = {
            "enable_thinking": preset.thinking.enabled,
            "preserve_thinking": preset.thinking.enabled,
        }
        if preset.thinking.enabled and model_id.startswith("qwen3.8-max"):
            body["reasoning_effort"] = {
                ReasoningEffort.LOW: "low",
                ReasoningEffort.MEDIUM: "medium",
                ReasoningEffort.HIGH: "xhigh",
                ReasoningEffort.MAX: "xhigh",
            }[preset.thinking.effort]
        if preset.native_search.enabled:
            body["enable_search"] = True
            body["search_options"] = {
                "forced_search": preset.native_search.forced,
                "search_strategy": preset.native_search.strategy,
            }
        return body

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
    ) -> QwenChatModel:
        del provider_name, model_name, preset_name
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
            "extra_body": self._extra_body(model.model_id, preset),
        }
        if preset.generation.temperature is not None:
            kwargs["temperature"] = preset.generation.temperature
        if preset.generation.top_p is not None:
            kwargs["top_p"] = preset.generation.top_p
        return QwenChatModel(**kwargs)
