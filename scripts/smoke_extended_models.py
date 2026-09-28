"""Run low-cost, sanitized real-API smoke checks for Kimi and GLM profiles."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage, ToolMessage, message_chunk_to_message

from core.model_runtime.factory import ModelFactory
from core.model_runtime.reporters import (
    InMemoryModelUsageReporter,
    ModelUsageReporterSlot,
)
from core.model_runtime.usage import UsageAttributionContext, bind_usage_attribution


ADD_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "add_numbers",
        "description": "Add two integers and return their sum.",
        "parameters": {
            "type": "object",
            "properties": {
                "a": {"type": "integer"},
                "b": {"type": "integer"},
            },
            "required": ["a", "b"],
            "additionalProperties": False,
        },
    },
}


def _attribution(provider: str, purpose: str) -> UsageAttributionContext:
    suffix = uuid4().hex
    return UsageAttributionContext(
        request_id=f"p4-smoke-{provider}-{suffix}",
        user_id="p4-smoke",
        workspace_id="p4-smoke",
        conversation_id=f"p4-smoke-{suffix}",
        turn_id=suffix,
        purpose=purpose,
    )


def _event_result(
    reporter: InMemoryModelUsageReporter,
    *,
    event_index: int,
    provider: str,
    model: str,
) -> dict[str, Any]:
    events = reporter.events[event_index:]
    if len(events) != 1:
        raise AssertionError(f"expected one usage event, got {len(events)}")
    invocation, usage, outcome = events[0]
    if invocation.identity.provider != provider:
        raise AssertionError(f"unexpected provider: {invocation.identity.provider}")
    if invocation.identity.provider_model != model:
        raise AssertionError(f"unexpected model: {invocation.identity.provider_model}")
    if usage.source != "provider" or usage.semantics != "final":
        raise AssertionError(
            f"usage is not exact provider-final data: {usage.source}/{usage.semantics}"
        )
    if usage.input_tokens <= 0 or usage.total_tokens <= 0:
        raise AssertionError("provider usage must contain positive input and total tokens")
    if usage.total_tokens != usage.input_tokens + usage.output_tokens:
        raise AssertionError("canonical total token invariant failed")
    if outcome.status != "succeeded":
        raise AssertionError(f"unexpected invocation outcome: {outcome.status}")
    return {
        "provider": provider,
        "model": model,
        "pricing_key": invocation.identity.pricing_key,
        "attempt": invocation.attempt,
        "finish_reason": outcome.finish_reason,
        "usage": {
            "input_tokens": usage.input_tokens,
            "cached_input_tokens": usage.cached_input_tokens,
            "cache_write_input_tokens": usage.cache_write_input_tokens,
            "output_tokens": usage.output_tokens,
            "reasoning_output_tokens": usage.reasoning_output_tokens,
            "total_tokens": usage.total_tokens,
            "source": usage.source,
            "semantics": usage.semantics,
            "has_provider_response_id": bool(usage.provider_response_id),
        },
    }


async def _collect_stream(model: Any, input_: Any) -> dict[str, Any]:
    chunks = []
    reasoning_indices = []
    content_indices = []
    tool_chunk_count = 0
    raw_usage_keys: set[str] = set()
    async for chunk in model.astream(input_):
        chunk_index = len(chunks)
        chunks.append(chunk)
        additional = getattr(chunk, "additional_kwargs", None) or {}
        if additional.get("reasoning_content"):
            reasoning_indices.append(chunk_index)
        if getattr(chunk, "content", None):
            content_indices.append(chunk_index)
        tool_chunk_count += len(getattr(chunk, "tool_call_chunks", None) or [])
        raw_usage = additional.get("provider_usage_raw")
        if isinstance(raw_usage, dict):
            raw_usage_keys.update(raw_usage)
    if not chunks:
        raise AssertionError("provider stream returned no chunks")
    combined = chunks[0]
    for chunk in chunks[1:]:
        combined += chunk
    return {
        "message": message_chunk_to_message(combined),
        "reasoning_indices": reasoning_indices,
        "content_indices": content_indices,
        "tool_chunk_count": tool_chunk_count,
        "raw_usage_keys": sorted(raw_usage_keys),
    }


async def _smoke_glm(
    factory: ModelFactory,
    reporter: InMemoryModelUsageReporter,
) -> dict[str, Any]:
    worker = factory.build_profile_role("glm", "worker")
    worker_payload = worker.candidates[0].model._get_request_payload(
        "Call add_numbers with a=2 and b=3. Do not answer without the tool."
    )
    worker_extra_body = worker_payload.get("extra_body") or {}
    if worker_extra_body.get("thinking") != {"type": "enabled"}:
        raise AssertionError("glm-5.3 thinking payload is not enabled")
    if worker_extra_body.get("tool_stream") is not True:
        raise AssertionError("glm-5.3 tool_stream is not enabled")

    bound_worker = worker.bind_tools([ADD_TOOL], tool_choice="add_numbers")
    event_index = len(reporter.events)
    chunks = []
    reasoning_chunk_count = 0
    tool_chunk_count = 0
    with bind_usage_attribution(_attribution("glm", "worker")):
        async for chunk in bound_worker.astream(
            "Call add_numbers with a=2 and b=3. Do not answer without the tool."
        ):
            chunks.append(chunk)
            if (getattr(chunk, "additional_kwargs", None) or {}).get(
                "reasoning_content"
            ):
                reasoning_chunk_count += 1
            tool_chunk_count += len(getattr(chunk, "tool_call_chunks", None) or [])
    if not chunks:
        raise AssertionError("glm-5.3 stream returned no chunks")
    combined = chunks[0]
    for chunk in chunks[1:]:
        combined += chunk
    tool_calls = getattr(combined, "tool_calls", None) or []
    if tool_chunk_count == 0 or len(tool_calls) != 1:
        raise AssertionError("ChatOpenAI did not parse the streamed GLM tool call")
    parsed_call = tool_calls[0]
    if parsed_call.get("name") != "add_numbers" or parsed_call.get("args") != {
        "a": 2,
        "b": 3,
    }:
        raise AssertionError(f"unexpected parsed GLM tool call: {parsed_call!r}")
    worker_result = _event_result(
        reporter,
        event_index=event_index,
        provider="glm",
        model="glm-5.3",
    )
    worker_result.update(
        {
            "thinking_enabled": True,
            "tool_stream": True,
            "reasoning_chunk_count": reasoning_chunk_count,
            "tool_call_chunk_count": tool_chunk_count,
            "parsed_tool_call": True,
        }
    )

    utility = factory.build_profile_role("glm", "utility")
    utility_payload = utility.candidates[0].model._get_request_payload(
        "Reply with exactly OK."
    )
    utility_extra_body = utility_payload.get("extra_body") or {}
    if utility_extra_body.get("thinking") != {"type": "disabled"}:
        raise AssertionError("glm-5.2 thinking payload is not disabled")
    if utility_payload.get("temperature") != 0.1:
        raise AssertionError("glm-5.2 temperature is not 0.1")
    event_index = len(reporter.events)
    with bind_usage_attribution(_attribution("glm", "other")):
        response = await utility.ainvoke("Reply with exactly OK.")
    if not str(response.content).strip():
        raise AssertionError("glm-5.2 returned empty content")
    utility_result = _event_result(
        reporter,
        event_index=event_index,
        provider="glm",
        model="glm-5.2",
    )
    utility_result.update(
        {
            "thinking_enabled": False,
            "temperature": 0.1,
            "non_empty_content": True,
        }
    )
    return {"glm_5_3_tool_stream": worker_result, "glm_5_2_utility": utility_result}


async def _smoke_kimi(
    factory: ModelFactory,
    reporter: InMemoryModelUsageReporter,
) -> dict[str, Any]:
    worker = factory.build_profile_role("kimi", "worker")
    worker_payload = worker.candidates[0].model._get_request_payload(
        "Compute 17 times 19, then reply with only the integer."
    )
    worker_extra_body = worker_payload.get("extra_body") or {}
    if worker_extra_body.get("thinking") != {"type": "enabled", "keep": None}:
        raise AssertionError("Kimi thinking payload is not enabled with keep=null")
    forbidden = {"temperature", "top_p", "n", "reasoning_effort"}
    if forbidden.intersection(worker_payload):
        raise AssertionError("Kimi request contains a forbidden sampling/effort field")

    event_index = len(reporter.events)
    with bind_usage_attribution(_attribution("kimi", "worker")):
        worker_stream = await _collect_stream(
            worker,
            "Compute 17 times 19, then reply with only the integer.",
        )
    if not worker_stream["reasoning_indices"] or not worker_stream["content_indices"]:
        raise AssertionError("Kimi thinking stream must contain reasoning and content")
    if min(worker_stream["reasoning_indices"]) >= min(worker_stream["content_indices"]):
        raise AssertionError("Kimi reasoning_content did not precede visible content")
    if "cached_tokens" not in worker_stream["raw_usage_keys"]:
        raise AssertionError("Kimi usage response omitted cached_tokens")
    worker_result = _event_result(
        reporter,
        event_index=event_index,
        provider="kimi",
        model="kimi-k2.6",
    )
    worker_result.update(
        {
            "thinking_enabled": True,
            "reasoning_before_content": True,
            "raw_usage_has_cached_tokens": True,
        }
    )

    utility = factory.build_profile_role("kimi", "utility")
    utility_payload = utility.candidates[0].model._get_request_payload(
        "Reply with exactly OK."
    )
    utility_extra_body = utility_payload.get("extra_body") or {}
    if utility_extra_body.get("thinking") != {"type": "disabled", "keep": None}:
        raise AssertionError("Kimi utility thinking payload is not disabled")
    event_index = len(reporter.events)
    with bind_usage_attribution(_attribution("kimi", "other")):
        utility_stream = await _collect_stream(utility, "Reply with exactly OK.")
    if utility_stream["reasoning_indices"]:
        raise AssertionError("Kimi thinking-disabled stream returned reasoning_content")
    if not utility_stream["content_indices"]:
        raise AssertionError("Kimi utility returned empty content")
    utility_result = _event_result(
        reporter,
        event_index=event_index,
        provider="kimi",
        model="kimi-k2.6",
    )
    utility_result.update(
        {
            "thinking_enabled": False,
            "reasoning_chunk_count": 0,
            "non_empty_content": True,
        }
    )

    bound_worker = worker.bind_tools([ADD_TOOL], tool_choice="add_numbers")
    event_index = len(reporter.events)
    question = HumanMessage(
        content="Use add_numbers with a=2 and b=3, then report the tool result."
    )
    with bind_usage_attribution(_attribution("kimi", "worker")):
        tool_stream = await _collect_stream(bound_worker, [question])
    assistant = tool_stream["message"]
    tool_calls = getattr(assistant, "tool_calls", None) or []
    reasoning = (getattr(assistant, "additional_kwargs", None) or {}).get(
        "reasoning_content"
    )
    if tool_stream["tool_chunk_count"] == 0 or len(tool_calls) != 1:
        raise AssertionError("Kimi streamed tool call was not parsed")
    if not reasoning:
        raise AssertionError("Kimi tool call omitted reasoning_content needed for pass-back")
    first_call_result = _event_result(
        reporter,
        event_index=event_index,
        provider="kimi",
        model="kimi-k2.6",
    )

    tool_call = tool_calls[0]
    history = [
        question,
        assistant,
        ToolMessage(content="5", tool_call_id=tool_call["id"]),
    ]
    history_payload = worker.candidates[0].model._get_request_payload(history)
    history_messages = history_payload.get("messages") or []
    if len(history_messages) < 2 or history_messages[1].get(
        "reasoning_content"
    ) != reasoning:
        raise AssertionError("Kimi tool-loop payload did not pass reasoning_content back")

    follow_up = worker.bind_tools([ADD_TOOL])
    event_index = len(reporter.events)
    with bind_usage_attribution(_attribution("kimi", "worker")):
        final_stream = await _collect_stream(follow_up, history)
    if not final_stream["content_indices"]:
        raise AssertionError("Kimi tool loop returned no final content")
    second_call_result = _event_result(
        reporter,
        event_index=event_index,
        provider="kimi",
        model="kimi-k2.6",
    )
    return {
        "kimi_thinking_stream": worker_result,
        "kimi_utility": utility_result,
        "kimi_tool_loop": {
            "first_call": first_call_result,
            "second_call": second_call_result,
            "tool_call_chunk_count": tool_stream["tool_chunk_count"],
            "reasoning_pass_back": True,
            "final_content": True,
        },
    }


async def _run(provider: str) -> dict[str, Any]:
    reporter = InMemoryModelUsageReporter()
    factory = ModelFactory.from_settings()
    factory.reporter_slot = ModelUsageReporterSlot(reporter, required=True)
    result: dict[str, Any] = {}
    if provider in {"glm", "all"}:
        if not factory.profile_available("glm"):
            raise RuntimeError("GLM_API_KEY is not configured")
        result.update(await _smoke_glm(factory, reporter))
    if provider in {"kimi", "all"}:
        if not factory.profile_available("kimi"):
            raise RuntimeError("KIMI_API_KEY is not configured")
        result.update(await _smoke_kimi(factory, reporter))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run sanitized real-API smoke checks for extended model profiles."
    )
    parser.add_argument(
        "--provider",
        choices=("glm", "kimi", "all"),
        default="all",
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(args.provider)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
