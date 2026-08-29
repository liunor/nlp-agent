"""Unit tests for Provider usage normalization, field mapping, and Response ID extraction."""

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from core.model_runtime.adapters.deepseek import DeepSeekChatModel
from core.model_runtime.adapters.openai_compatible import OpenAICompatibleChatModel
from core.model_runtime.adapters.qwen import QwenChatModel
from core.model_runtime.normalization import (
    canonical_usage,
    error_canonical_usage,
    extract_provider_response_id,
    normalize_chunk,
    normalize_message,
    normalize_usage,
    response_canonical_usage,
)


def test_canonical_usage_deepseek_fields():
    raw_deepseek = {
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "total_tokens": 1200,
        "prompt_cache_hit_tokens": 600,
        "prompt_cache_miss_tokens": 400,
        "completion_tokens_details": {
            "reasoning_tokens": 80,
        },
    }
    canon = canonical_usage(raw_deepseek, provider_response_id="deepseek-resp-1")
    assert canon.input_tokens == 1000
    assert canon.cached_input_tokens == 600
    assert canon.cache_write_input_tokens == 0
    assert canon.output_tokens == 200
    assert canon.reasoning_output_tokens == 80
    assert canon.total_tokens == 1200
    assert canon.source == "provider"
    assert canon.provider_response_id == "deepseek-resp-1"

    # Observability normalize_usage still retains prompt_cache_miss_tokens
    norm = normalize_usage(raw_deepseek)
    assert norm["prompt_cache_miss_tokens"] == 400
    assert norm["prompt_cache_hit_tokens"] == 600


def test_canonical_usage_qwen_fields():
    raw_qwen = {
        "prompt_tokens": 500,
        "completion_tokens": 150,
        "total_tokens": 650,
        "prompt_tokens_details": {
            "cached_tokens": 250,
        },
        "completion_tokens_details": {
            "reasoning_tokens": 50,
        },
    }
    canon = canonical_usage(raw_qwen, provider_response_id="qwen-resp-1")
    assert canon.input_tokens == 500
    assert canon.cached_input_tokens == 250
    assert canon.output_tokens == 150
    assert canon.reasoning_output_tokens == 50
    assert canon.total_tokens == 650
    assert canon.source == "provider"
    assert canon.provider_response_id == "qwen-resp-1"


def test_canonical_usage_raw_total_mismatch_is_recalculated():
    raw_inconsistent = {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 9999,  # Inconsistent raw total
    }
    canon = canonical_usage(raw_inconsistent)
    assert canon.input_tokens == 100
    assert canon.output_tokens == 50
    assert canon.total_tokens == 150  # Canonical calculates input + output


def test_canonical_usage_empty_and_none():
    canon_none = canonical_usage(None)
    assert canon_none.source == "none"
    assert canon_none.total_tokens == 0

    canon_empty = canonical_usage({})
    assert canon_empty.source == "none"
    assert canon_empty.total_tokens == 0

    canon_provider_zero = canonical_usage({}, source="provider")
    assert canon_provider_zero.source == "provider"
    assert canon_provider_zero.total_tokens == 0


def test_canonical_usage_rejects_invalid_types():
    with pytest.raises(ValueError, match="Invalid boolean value"):
        canonical_usage({"prompt_tokens": True, "completion_tokens": 10})

    with pytest.raises(ValueError, match="Invalid float value"):
        canonical_usage({"prompt_tokens": 10.5, "completion_tokens": 10})

    with pytest.raises(ValueError, match="Negative token count"):
        canonical_usage({"prompt_tokens": -5, "completion_tokens": 10})


def test_error_canonical_usage_reads_nested_provider_payload():
    error = RuntimeError("provider failed after accounting")
    error.body = {
        "request_id": "provider-request-1",
        "error": {
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 4,
                "total_tokens": 99,
            }
        },
    }

    usage = error_canonical_usage(error)

    assert usage.source == "provider"
    assert usage.input_tokens == 11
    assert usage.output_tokens == 4
    assert usage.total_tokens == 15
    assert usage.provider_response_id == "provider-request-1"


def test_extract_provider_response_id():
    msg1 = AIMessage(
        content="hello",
        response_metadata={"provider_response_id": "p-123", "id": "alt-id"},
    )
    assert extract_provider_response_id(msg1) == "p-123"

    msg2 = AIMessage(
        content="hello",
        response_metadata={"id": "alt-id"},
    )
    assert extract_provider_response_id(msg2) == "alt-id"

    msg3 = AIMessage(
        content="hello",
        additional_kwargs={"provider_response_id": "add-123"},
    )
    assert extract_provider_response_id(msg3) == "add-123"

    msg_empty = AIMessage(content="hello")
    assert extract_provider_response_id(msg_empty) is None


def test_response_canonical_usage_with_message():
    msg = AIMessage(
        content="hello",
        response_metadata={
            "provider_response_id": "resp-99",
            "token_usage": {
                "prompt_tokens": 50,
                "completion_tokens": 20,
            },
        },
    )
    canon = response_canonical_usage(msg)
    assert canon.provider_response_id == "resp-99"
    assert canon.input_tokens == 50
    assert canon.output_tokens == 20
    assert canon.total_tokens == 70
    assert canon.source == "provider"


def test_response_canonical_usage_validates_preserved_raw_provider_usage():
    msg = AIMessage(
        content="hello",
        usage_metadata={
            "input_tokens": 10,
            "output_tokens": 2,
            "total_tokens": 12,
        },
        additional_kwargs={
            "provider_usage_raw": {
                "prompt_tokens": 10.5,
                "completion_tokens": 2,
            }
        },
    )

    with pytest.raises(ValueError, match="Invalid float value"):
        response_canonical_usage(msg)


def test_deepseek_adapter_preserves_response_id():
    chat = DeepSeekChatModel(model="deepseek-chat", api_key="test")
    # Simulate _create_chat_result with an object/dict having id and usage
    raw_response = {
        "id": "chatcmpl-deepseek-123",
        "choices": [{"message": {"content": "hi", "role": "assistant"}}],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "prompt_cache_hit_tokens": 20,
        },
    }
    result = chat._create_chat_result(raw_response)
    msg = result.generations[0].message
    assert msg.response_metadata["provider_response_id"] == "chatcmpl-deepseek-123"
    assert msg.additional_kwargs["provider_response_id"] == "chatcmpl-deepseek-123"
    assert msg.additional_kwargs["provider_usage_raw"] == raw_response["usage"]
    assert msg.usage_metadata["input_tokens"] == 100


def test_qwen_adapter_preserves_response_id():
    chat = QwenChatModel(api_key="test")
    raw_response = {
        "id": "chatcmpl-qwen-456",
        "choices": [{"message": {"content": "hi", "role": "assistant"}}],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 60,
            "total_tokens": 180,
            "prompt_tokens_details": {"cached_tokens": 40},
        },
    }
    result = chat._create_chat_result(raw_response)
    msg = result.generations[0].message
    assert msg.response_metadata["provider_response_id"] == "chatcmpl-qwen-456"
    assert msg.additional_kwargs["provider_response_id"] == "chatcmpl-qwen-456"
    assert msg.additional_kwargs["provider_usage_raw"] == raw_response["usage"]
    assert msg.usage_metadata["input_tokens"] == 120


def test_openai_compatible_adapter_preserves_response_id():
    chat = OpenAICompatibleChatModel(api_key="test")
    raw_response = {
        "id": "chatcmpl-openai-789",
        "choices": [{"message": {"content": "hi", "role": "assistant"}}],
        "usage": {
            "prompt_tokens": 80,
            "completion_tokens": 30,
            "total_tokens": 110,
        },
    }
    result = chat._create_chat_result(raw_response)
    msg = result.generations[0].message
    assert msg.response_metadata["provider_response_id"] == "chatcmpl-openai-789"
    assert msg.additional_kwargs["provider_response_id"] == "chatcmpl-openai-789"
    assert msg.additional_kwargs["provider_usage_raw"] == raw_response["usage"]
    assert msg.usage_metadata["input_tokens"] == 80
