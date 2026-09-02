"""Normalize provider response metadata without repairing executable tool arguments."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk

from core.model_runtime.usage import (
    CanonicalTokenUsage,
    UsageSemantics,
    UsageSource,
)


_USAGE_SEMANTICS = {"final", "cumulative", "delta", "partial"}


def _usage_semantics(value: Any, default: UsageSemantics) -> UsageSemantics:
    return value if value in _USAGE_SEMANTICS else default  # type: ignore[return-value]


def _parse_token_int(value: Any, field_name: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        raise ValueError(f"Invalid boolean value for token field {field_name!r}")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"Negative token count for {field_name!r}: {value}")
        return value
    if isinstance(value, float):
        raise ValueError(f"Invalid float value for token field {field_name!r}: {value}")
    raise ValueError(
        f"Invalid token value type {type(value).__name__} for {field_name!r}: {value}"
    )


def _extract_usage_details(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(metadata or {})
    input_details = {
        k: v
        for k, v in (
            raw.get("input_token_details") or raw.get("prompt_tokens_details") or {}
        ).items()
        if v is not None
    }
    output_details = {
        k: v
        for k, v in (
            raw.get("output_token_details") or raw.get("completion_tokens_details") or {}
        ).items()
        if v is not None
    }
    return {
        "raw": raw,
        "input_details": input_details,
        "output_details": output_details,
        "raw_input": raw.get("input_tokens", raw.get("prompt_tokens", 0)),
        "raw_output": raw.get("output_tokens", raw.get("completion_tokens", 0)),
        "raw_cache_read": raw.get(
            "prompt_cache_hit_tokens",
            raw.get(
                "cached_tokens",
                raw.get(
                    "cache_read_input_tokens",
                    input_details.get(
                        "cache_read", input_details.get("cached_tokens", 0)
                    ),
                ),
            ),
        ),
        "raw_cache_write": raw.get(
            "cache_write_input_tokens",
            input_details.get(
                "cache_write", input_details.get("cache_creation_input_tokens", 0)
            ),
        ),
        "raw_cache_miss": raw.get(
            "prompt_cache_miss_tokens", input_details.get("cache_miss", 0)
        ),
        "raw_reasoning": raw.get(
            "reasoning_tokens",
            output_details.get("reasoning", output_details.get("reasoning_tokens", 0)),
        ),
        "raw_total": raw.get("total_tokens"),
    }


def normalize_usage(
    metadata: Mapping[str, Any] | None,
    *,
    default_semantics: UsageSemantics = "final",
) -> dict[str, Any]:
    extracted = _extract_usage_details(metadata)
    input_tokens = int(extracted["raw_input"] or 0)
    output_tokens = int(extracted["raw_output"] or 0)
    cache_read = int(extracted["raw_cache_read"] or 0)
    cache_miss = int(extracted["raw_cache_miss"] or 0)
    cache_write = int(extracted["raw_cache_write"] or 0)
    reasoning = int(extracted["raw_reasoning"] or 0)
    total_val = extracted["raw_total"]
    total = int(total_val if total_val is not None else (input_tokens + output_tokens))
    return {
        "input_tokens": max(0, input_tokens),
        "output_tokens": max(0, output_tokens),
        "total_tokens": max(0, total),
        "input_token_details": {
            **extracted["input_details"],
            "cache_read": max(0, cache_read),
            "cache_miss": max(0, cache_miss),
            "cache_write": max(0, cache_write),
        },
        "output_token_details": {
            **extracted["output_details"],
            "reasoning": max(0, reasoning),
        },
        "prompt_cache_hit_tokens": max(0, cache_read),
        "prompt_cache_miss_tokens": max(0, cache_miss),
        "cache_write_input_tokens": max(0, cache_write),
        "usage_semantics": _usage_semantics(
            extracted["raw"].get("usage_semantics")
            or extracted["raw"].get("semantics"),
            default_semantics,
        ),
    }


def canonical_usage(
    metadata: Mapping[str, Any] | None,
    *,
    provider_response_id: str | None = None,
    source: UsageSource | None = None,
    semantics: UsageSemantics = "final",
) -> CanonicalTokenUsage:
    if not metadata:
        if source == "provider":
            return CanonicalTokenUsage(
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                source="provider",
                semantics=semantics,
                provider_response_id=provider_response_id,
            )
        return CanonicalTokenUsage(
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            source="none",
            semantics=semantics,
            provider_response_id=provider_response_id,
        )

    extracted = _extract_usage_details(metadata)
    input_tokens = _parse_token_int(extracted["raw_input"], "input_tokens")
    output_tokens = _parse_token_int(extracted["raw_output"], "output_tokens")
    cached_input_tokens = _parse_token_int(
        extracted["raw_cache_read"], "cached_input_tokens"
    )
    cache_write_input_tokens = _parse_token_int(
        extracted["raw_cache_write"], "cache_write_input_tokens"
    )
    reasoning_output_tokens = _parse_token_int(
        extracted["raw_reasoning"], "reasoning_output_tokens"
    )
    total_tokens = input_tokens + output_tokens

    has_tokens = any((
        input_tokens,
        cached_input_tokens,
        cache_write_input_tokens,
        output_tokens,
        reasoning_output_tokens,
    ))

    resolved_source: UsageSource = (
        source
        if source is not None
        else ("provider" if (has_tokens or bool(extracted["raw"])) else "none")
    )

    return CanonicalTokenUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_write_input_tokens=cache_write_input_tokens,
        output_tokens=output_tokens,
        reasoning_output_tokens=reasoning_output_tokens,
        total_tokens=total_tokens,
        source=resolved_source,
        semantics=_usage_semantics(
            extracted["raw"].get("usage_semantics")
            or extracted["raw"].get("semantics"),
            semantics,
        ),
        provider_response_id=provider_response_id,
    )


def extract_provider_response_id(message: Any) -> str | None:
    resp_meta = getattr(message, "response_metadata", None) or {}
    if resp_meta.get("provider_response_id"):
        return str(resp_meta["provider_response_id"])
    add_kwargs = getattr(message, "additional_kwargs", None) or {}
    if add_kwargs.get("provider_response_id"):
        return str(add_kwargs["provider_response_id"])
    if resp_meta.get("id"):
        return str(resp_meta["id"])
    return None


def response_canonical_usage(message: Any) -> CanonicalTokenUsage:
    provider_response_id = extract_provider_response_id(message)
    add_kwargs = getattr(message, "additional_kwargs", None) or {}
    default_semantics: UsageSemantics = (
        "cumulative" if isinstance(message, AIMessageChunk) else "final"
    )
    raw_provider_usage = add_kwargs.get("provider_usage_raw")
    if raw_provider_usage is not None:
        return canonical_usage(
            raw_provider_usage,
            provider_response_id=provider_response_id,
            source="provider",
            semantics=_usage_semantics(
                add_kwargs.get("provider_usage_semantics"), default_semantics
            ),
        )
    direct = getattr(message, "usage_metadata", None)
    if direct:
        return canonical_usage(
            direct,
            provider_response_id=provider_response_id,
            source="provider",
            semantics=_usage_semantics(
                add_kwargs.get("provider_usage_semantics"), default_semantics
            ),
        )
    resp_meta = getattr(message, "response_metadata", None) or {}
    raw_usage = resp_meta.get("token_usage") or resp_meta.get("usage")
    if raw_usage:
        return canonical_usage(
            raw_usage,
            provider_response_id=provider_response_id,
            source="provider",
            semantics=default_semantics,
        )
    prov_usage = add_kwargs.get("provider_usage")
    if prov_usage:
        return canonical_usage(
            prov_usage,
            provider_response_id=provider_response_id,
            source="provider",
            semantics=_usage_semantics(
                add_kwargs.get("provider_usage_semantics"), default_semantics
            ),
        )
    return canonical_usage(
        None,
        provider_response_id=provider_response_id,
        source="none",
        semantics=("partial" if isinstance(message, AIMessageChunk) else "final"),
    )


def error_canonical_usage(error: BaseException) -> CanonicalTokenUsage:
    """Conservatively extract usage carried by a Provider error payload."""
    body = getattr(error, "body", None)
    if not isinstance(body, Mapping):
        return CanonicalTokenUsage(source="none", semantics="partial")

    raw_usage = body.get("usage")
    error_details = body.get("error")
    if raw_usage is None and isinstance(error_details, Mapping):
        raw_usage = error_details.get("usage")
    if raw_usage is None:
        return CanonicalTokenUsage(source="none", semantics="partial")
    if not isinstance(raw_usage, Mapping):
        return CanonicalTokenUsage(source="none", semantics="partial")

    response_id = body.get("id") or body.get("request_id")
    if response_id is None and isinstance(error_details, Mapping):
        response_id = error_details.get("id") or error_details.get("request_id")
    return canonical_usage(
        raw_usage,
        provider_response_id=str(response_id) if response_id is not None else None,
        source="provider",
        semantics="partial",
    )


def response_usage(message: Any) -> dict[str, Any]:
    default_semantics: UsageSemantics = (
        "cumulative" if isinstance(message, AIMessageChunk) else "final"
    )
    direct = getattr(message, "usage_metadata", None)
    if direct:
        return normalize_usage(direct, default_semantics=default_semantics)
    response = getattr(message, "response_metadata", None) or {}
    return normalize_usage(
        response.get("token_usage") or response.get("usage") or {},
        default_semantics=default_semantics,
    )


def normalize_message(message: AIMessage) -> AIMessage:
    usage = response_usage(message)
    response_metadata = dict(message.response_metadata or {})
    finish = response_metadata.get("finish_reason")
    if finish == "function_call":
        response_metadata["finish_reason"] = "tool_calls"
    updates: dict[str, Any] = {"response_metadata": response_metadata}
    if usage["total_tokens"] or usage["input_tokens"] or usage["output_tokens"]:
        updates["usage_metadata"] = {
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "total_tokens": usage["total_tokens"],
            "input_token_details": usage["input_token_details"],
            "output_token_details": usage["output_token_details"],
        }
        updates["additional_kwargs"] = {
            **message.additional_kwargs,
            "provider_usage": usage,
        }
    return message.model_copy(update=updates)


def normalize_chunk(chunk: AIMessageChunk) -> AIMessageChunk:
    usage = response_usage(chunk)
    if not (usage["input_tokens"] or usage["output_tokens"] or usage["total_tokens"]):
        return chunk
    return chunk.model_copy(
        update={
            "usage_metadata": {
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "total_tokens": usage["total_tokens"],
                "input_token_details": usage["input_token_details"],
                "output_token_details": usage["output_token_details"],
            },
            "additional_kwargs": {
                **chunk.additional_kwargs,
                "provider_usage": usage,
                "provider_usage_semantics": usage["usage_semantics"],
            },
        }
    )
