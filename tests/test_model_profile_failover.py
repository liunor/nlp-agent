"""Exercise the real profile factory path with isolated Provider responses."""

import asyncio
import time
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from langchain_core.messages import AIMessageChunk, HumanMessage

from core.model_runtime.factory import ModelFactory
from core.model_runtime.reporters import (
    InMemoryModelUsageReporter,
    ModelUsageReporterSlot,
)
from core.model_runtime.runtime import (
    ModelRuntimeExhaustedError,
    StreamInterruptedError,
)
from core.model_runtime.usage import UsageAttributionContext, bind_usage_attribution


class ProviderError(RuntimeError):
    def __init__(self, status):
        super().__init__("isolated Provider response")
        self.status_code = status
        self.headers = {"Retry-After": "0"}


class FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def astream(self, _input, config=None, **kwargs):
        self.calls += 1
        response = self.responses.pop(0)
        for item in response:
            if isinstance(item, BaseException):
                raise item
            yield item

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, **kwargs):
        return self


def raw_config():
    return yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "configs/agent_config.yaml").read_text(
            encoding="utf-8"
        )
    )


def factory_with_providers(monkeypatch, providers=None, raw=None, reporter=None):
    config = deepcopy(raw or raw_config())
    for preset in config["model_presets"].values():
        preset["retry"] = {
            "max_attempts": 2,
            "base_delay_s": 0,
            "max_delay_s": 0,
            "jitter": "none",
        }
    factory = ModelFactory(
        ModelFactory.config_from_raw(config),
        reporter_slot=ModelUsageReporterSlot(reporter),
    )
    monkeypatch.setattr(factory, "_api_key", lambda _name: "isolated-unused-key")
    monkeypatch.setattr(
        factory.registry,
        "build",
        lambda adapter, **kwargs: (providers or {}).get(
            kwargs["preset_name"], FakeProvider([])
        ),
    )
    return factory


@pytest.mark.parametrize(
    "role,expected",
    [
        ("coordinator", ["coordinator-pro", "coordinator-fast"]),
        ("worker", ["worker-flash", "worker-pro"]),
        ("utility", ["utility-flash"]),
    ],
)
def test_selected_profile_keeps_its_configured_same_provider_route(
    monkeypatch, role, expected
):
    factory = factory_with_providers(monkeypatch)
    runtime = factory.build_profile_role("deepseek", role, thinking_enabled=False)
    assert [candidate.preset_name for candidate in runtime.candidates] == expected
    assert {candidate.provider_name for candidate in runtime.candidates} == {"deepseek"}
    assert all(
        not candidate.preset.thinking.enabled for candidate in runtime.candidates
    )
    assert runtime.model_profile == "deepseek"
    assert runtime is factory.build_profile_role(
        "deepseek", role, thinking_enabled=False
    )
    assert len(runtime.bind_tools([]).candidates) == len(expected)
    assert len(runtime.with_structured_output(dict).candidates) == len(expected)
    assert runtime.context_window_tokens == min(
        c.definition.context_window_tokens for c in runtime.candidates
    )
    assert runtime.max_output_tokens == max(
        c.preset.generation.max_output_tokens for c in runtime.candidates
    )


def test_profile_never_inherits_another_provider_or_specialized_route(monkeypatch):
    raw = raw_config()
    raw["model_routes"]["coordinator"]["fallbacks"] = [
        "coordinator-qwen-max",
        "coordinator-fast",
    ]
    factory = factory_with_providers(monkeypatch, raw=raw)
    assert [
        c.preset_name
        for c in factory.build_profile_role("deepseek", "coordinator").candidates
    ] == ["coordinator-pro", "coordinator-fast"]
    assert [
        c.preset_name
        for c in factory.build_profile_role("qwen", "coordinator").candidates
    ] == ["coordinator-qwen-max"]
    assert [
        c.preset_name for c in factory.build_override("worker-qwen-web").candidates
    ] == ["worker-qwen-web"]
    assert [
        c.preset_name for c in factory.build_preset("coordinator-pro").candidates
    ] == ["coordinator-pro"]


def test_runtime_profile_override_does_not_silently_switch_primary(monkeypatch):
    raw = raw_config()
    raw["model_profiles"]["deepseek"]["coordinator"] = "coordinator-fast"
    factory = factory_with_providers(monkeypatch, raw=raw)
    assert [
        c.preset_name
        for c in factory.build_profile_role("deepseek", "coordinator").candidates
    ] == ["coordinator-fast"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        ProviderError(503),
        ProviderError(429),
        TimeoutError("isolated timeout"),
        ConnectionResetError("isolated connection"),
    ],
)
async def test_profile_transient_failure_reaches_fallback_and_reports_actual_usage(
    monkeypatch, failure
):
    primary = FakeProvider([[failure], [failure]])
    fallback = FakeProvider(
        [
            [
                AIMessageChunk(
                    content="recovered",
                    usage_metadata={
                        "input_tokens": 2,
                        "output_tokens": 1,
                        "total_tokens": 3,
                    },
                )
            ]
        ]
    )
    reporter = InMemoryModelUsageReporter()
    factory = factory_with_providers(
        monkeypatch,
        {"coordinator-pro": primary, "coordinator-fast": fallback},
        reporter=reporter,
    )
    runtime = factory.build_profile_role("deepseek", "coordinator")
    with bind_usage_attribution(
        UsageAttributionContext(
            request_id="isolated-request",
            user_id="isolated-user",
            workspace_id="isolated-workspace",
            conversation_id="isolated-conversation",
            turn_id="isolated-turn",
            purpose="coordinator",
        )
    ):
        result = await runtime.ainvoke([HumanMessage(content="isolated input")])
    assert result.content == "recovered"
    assert (primary.calls, fallback.calls) == (2, 1)
    assert len(reporter.events) == 3
    assert len({invocation.operation_id for invocation, _, _ in reporter.events}) == 3
    assert [outcome.status for _, _, outcome in reporter.events] == [
        "failed",
        "failed",
        "succeeded",
    ]
    assert [invocation.fallback_index for invocation, _, _ in reporter.events] == [
        0,
        0,
        1,
    ]
    assert [
        invocation.identity.provider_model for invocation, _, _ in reporter.events
    ] == ["deepseek-v4-pro", "deepseek-v4-pro", "deepseek-v4-flash"]
    assert sum(usage.total_tokens for _, usage, _ in reporter.events) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_profile_permanent_error_never_retries_or_fails_over(monkeypatch, status):
    primary, fallback = FakeProvider([[ProviderError(status)]]), FakeProvider([])
    runtime = factory_with_providers(
        monkeypatch, {"coordinator-pro": primary, "coordinator-fast": fallback}
    ).build_profile_role("deepseek", "coordinator")
    with pytest.raises(ProviderError):
        await runtime.ainvoke([HumanMessage(content="isolated input")])
    assert (primary.calls, fallback.calls) == (1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chunk",
    [
        AIMessageChunk(content="partial answer"),
        AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "name": "isolated_tool",
                    "args": '{"x":',
                    "id": "isolated-call",
                    "index": 0,
                }
            ],
        ),
    ],
)
async def test_profile_visible_content_or_tool_arguments_never_replay(
    monkeypatch, chunk
):
    primary, fallback = FakeProvider([[chunk, ProviderError(503)]]), FakeProvider([])
    runtime = factory_with_providers(
        monkeypatch, {"coordinator-pro": primary, "coordinator-fast": fallback}
    ).build_profile_role("deepseek", "coordinator")
    stream = runtime.astream([HumanMessage(content="isolated input")])
    await anext(stream)
    with pytest.raises(StreamInterruptedError):
        await anext(stream)
    assert (primary.calls, fallback.calls) == (1, 0)


@pytest.mark.asyncio
async def test_profile_all_candidates_fail_with_bounded_attempts(monkeypatch):
    primary, fallback = (
        FakeProvider([[ProviderError(503)], [ProviderError(503)]]),
        FakeProvider([[ProviderError(503)], [ProviderError(503)]]),
    )
    runtime = factory_with_providers(
        monkeypatch, {"coordinator-pro": primary, "coordinator-fast": fallback}
    ).build_profile_role("deepseek", "coordinator")
    with pytest.raises(ModelRuntimeExhaustedError):
        await runtime.ainvoke([HumanMessage(content="isolated input")])
    assert (primary.calls, fallback.calls) == (2, 2)


@pytest.mark.asyncio
async def test_profile_cancellation_does_not_start_another_model(monkeypatch):
    primary, fallback = FakeProvider([[asyncio.CancelledError()]]), FakeProvider([])
    runtime = factory_with_providers(
        monkeypatch, {"coordinator-pro": primary, "coordinator-fast": fallback}
    ).build_profile_role("deepseek", "coordinator")
    with pytest.raises(asyncio.CancelledError):
        await runtime.ainvoke([HumanMessage(content="isolated input")])
    assert (primary.calls, fallback.calls) == (1, 0)


@pytest.mark.asyncio
async def test_profile_open_circuit_uses_available_fallback(monkeypatch):
    primary = FakeProvider([])
    fallback = FakeProvider([[AIMessageChunk(content="available")]])
    runtime = factory_with_providers(
        monkeypatch, {"coordinator-pro": primary, "coordinator-fast": fallback}
    ).build_profile_role("deepseek", "coordinator")
    runtime.candidates[0].circuit.open_until = time.monotonic() + 60
    result = await runtime.ainvoke([HumanMessage(content="isolated input")])
    assert result.content == "available"
    assert (primary.calls, fallback.calls) == (0, 1)


@pytest.mark.parametrize("status,expected", [(400, "upstream_invalid_request"), (401, "upstream_auth_failed"), (403, "upstream_auth_failed"), (404, "upstream_model_unavailable")])
def test_turn_terminal_classifies_permanent_provider_error(status, expected):
    import httpx
    from openai import APIStatusError
    from gateway.turn_execution import _error_kind_for_turn

    error = APIStatusError("isolated Provider error", response=httpx.Response(status, request=httpx.Request("POST", "http://127.0.0.1/isolated")), body=None)
    assert _error_kind_for_turn(error) == expected
