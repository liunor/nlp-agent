import asyncio
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from core.model_runtime.adapters.deepseek import DeepSeekChatModel
from core.model_runtime.adapters.qwen import QwenAdapter
from core.model_runtime.contracts import (
    CircuitBreakerPolicy,
    GenerationConfig,
    ModelCapabilities,
    ModelDefinition,
    ModelPresetConfig,
    ModelProfileConfig,
    ModelRouteConfig,
    ModelRuntimeConfig,
    NativeSearchConfig,
    ProviderConfig,
    RetryPolicy,
    ThinkingConfig,
    TimeoutPolicy,
)
from core.model_runtime.normalization import normalize_usage
from core.model_runtime.runtime import (
    ModelCandidate,
    ResilientChatModel,
    StreamInterruptedError,
    classify_model_error,
)


def preset(*, attempts=1):
    return ModelPresetConfig(
        model="model",
        thinking=ThinkingConfig(enabled=False, effort="none"),
        generation=GenerationConfig(max_output_tokens=100),
        timeouts=TimeoutPolicy(connect_s=1, first_token_s=1, stream_idle_s=1, total_s=2),
        retry=RetryPolicy(max_attempts=attempts, base_delay_s=0, max_delay_s=0, jitter="none"),
        circuit_breaker=CircuitBreakerPolicy(failure_threshold=10, cooldown_s=1),
    )


def definition(model_id="model"):
    return ModelDefinition(
        provider="test", model_id=model_id,
        context_window_tokens=1000, max_output_tokens=100,
        capabilities=ModelCapabilities(thinking=True),
    )


def candidate(name, model, *, attempts=1):
    return ModelCandidate(
        preset_name=name, provider_name="test", model_name=name,
        definition=definition(name), preset=preset(attempts=attempts), model=model,
    )


class StatusError(RuntimeError):
    def __init__(self, status_code, message="failed"):
        super().__init__(message)
        self.status_code = status_code


class FakeModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.bound_tools = None

    async def ainvoke(self, _input, config=None, **_kwargs):
        self.calls += 1
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    async def astream(self, input, config=None, **kwargs):
        yield await self.ainvoke(input, config=config, **kwargs)

    def bind_tools(self, tools, **_kwargs):
        self.bound_tools = tools
        return self

    def with_structured_output(self, _schema, **_kwargs):
        return self


class FakeStreamModel(FakeModel):
    def __init__(self, chunks):
        super().__init__([])
        self.chunks = chunks

    async def astream(self, _input, config=None, **_kwargs):
        self.calls += 1
        for value in self.chunks:
            if isinstance(value, BaseException):
                raise value
            yield value


def test_typed_config_rejects_incapable_fallback():
    with pytest.raises(ValueError, match="tool-call capability"):
        ModelRuntimeConfig(
            providers={"test": ProviderConfig(adapter="x", base_url="http://test", api_key_env="KEY")},
            models={
                "primary": ModelDefinition(
                    provider="test", model_id="primary", context_window_tokens=100,
                    max_output_tokens=10, capabilities=ModelCapabilities(tool_calls=True),
                ),
                "fallback": ModelDefinition(
                    provider="test", model_id="fallback", context_window_tokens=100,
                    max_output_tokens=10, capabilities=ModelCapabilities(tool_calls=False),
                ),
            },
            model_presets={
                "p": ModelPresetConfig(model="primary", generation=GenerationConfig(max_output_tokens=10)),
                "f": ModelPresetConfig(model="fallback", generation=GenerationConfig(max_output_tokens=10)),
            },
            model_routes={"coordinator": ModelRouteConfig(primary="p", fallbacks=("f",))},
        )


@pytest.mark.parametrize(
    ("capability", "error_label"),
    (("vision", "vision"), ("structured_output", "structured-output")),
)
def test_typed_config_rejects_fallback_missing_vision_contract(
    capability: str,
    error_label: str,
):
    with pytest.raises(ValueError, match=rf"lacks {error_label} capability"):
        ModelRuntimeConfig(
            providers={"test": ProviderConfig(adapter="x", base_url="http://test", api_key_env="KEY")},
            models={
                "primary": ModelDefinition(
                    provider="test",
                    model_id="primary",
                    context_window_tokens=100,
                    max_output_tokens=10,
                    capabilities=ModelCapabilities(**{capability: True}),
                ),
                "fallback": ModelDefinition(
                    provider="test",
                    model_id="fallback",
                    context_window_tokens=100,
                    max_output_tokens=10,
                    capabilities=ModelCapabilities(),
                ),
            },
            model_presets={
                "p": ModelPresetConfig(model="primary", generation=GenerationConfig(max_output_tokens=10)),
                "f": ModelPresetConfig(model="fallback", generation=GenerationConfig(max_output_tokens=10)),
            },
            model_routes={"vision-worker": ModelRouteConfig(primary="p", fallbacks=("f",))},
        )


def test_model_profile_rejects_a_preset_from_another_provider():
    with pytest.raises(ValueError, match="expected 'qwen'"):
        ModelRuntimeConfig(
            providers={
                "deepseek": ProviderConfig(
                    adapter="deepseek", base_url="http://deepseek", api_key_env="DEEPSEEK_KEY"
                ),
                "qwen": ProviderConfig(
                    adapter="qwen", base_url="http://qwen", api_key_env="QWEN_KEY"
                ),
            },
            models={
                "deepseek-model": ModelDefinition(
                    provider="deepseek", model_id="deepseek", context_window_tokens=100,
                    max_output_tokens=10,
                ),
                "qwen-model": ModelDefinition(
                    provider="qwen", model_id="qwen", context_window_tokens=100,
                    max_output_tokens=10,
                ),
            },
            model_presets={
                "deepseek-preset": ModelPresetConfig(
                    model="deepseek-model", generation=GenerationConfig(max_output_tokens=10)
                ),
                "qwen-preset": ModelPresetConfig(
                    model="qwen-model", generation=GenerationConfig(max_output_tokens=10)
                ),
            },
            model_routes={},
            model_profiles={
                "qwen": ModelProfileConfig(
                    label="Qwen", provider="qwen", coordinator="qwen-preset",
                    worker="qwen-preset", utility="deepseek-preset",
                )
            },
            default_model_profile="qwen",
        )


def test_native_search_requires_an_enabled_qwen_preset():
    with pytest.raises(ValueError, match="requires native_search.enabled=true"):
        NativeSearchConfig(enabled=False, forced=True)
    with pytest.raises(ValueError, match="Input should be 'turbo' or 'max'"):
        NativeSearchConfig(enabled=True, strategy="agent")

    with pytest.raises(ValueError, match="unsupported adapter 'deepseek'"):
        ModelRuntimeConfig(
            providers={
                "deepseek": ProviderConfig(
                    adapter="deepseek", base_url="http://deepseek", api_key_env="KEY"
                )
            },
            models={
                "model": ModelDefinition(
                    provider="deepseek", model_id="model", context_window_tokens=100,
                    max_output_tokens=10,
                )
            },
            model_presets={
                "web": ModelPresetConfig(
                    model="model",
                    generation=GenerationConfig(max_output_tokens=10),
                    native_search=NativeSearchConfig(enabled=True),
                )
            },
            model_routes={},
        )


def test_project_qwen_web_preset_is_valid_and_other_qwen_presets_stay_offline():
    import yaml

    root = Path(__file__).resolve().parents[1]
    raw = yaml.safe_load(
        (root / "configs" / "agent_config.yaml").read_text(encoding="utf-8")
    )
    config = ModelRuntimeConfig.model_validate(
        {
            "providers": raw["providers"],
            "models": raw["models"],
            "model_presets": raw["model_presets"],
            "model_routes": raw["model_routes"],
            "model_profiles": raw["model_profiles"],
            "default_model_profile": raw["defaults"]["model_profile"],
        }
    )

    assert config.providers["qwen"].base_url == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    web_preset = config.preset("worker-qwen-web")
    assert web_preset.native_search == NativeSearchConfig(
        enabled=True, forced=True, strategy="turbo"
    )
    assert web_preset.retry.max_attempts == 1
    assert config.preset("worker-qwen-plus").native_search.enabled is False
    assert config.preset("coordinator-qwen-max").native_search.enabled is False
    assert config.preset("utility-qwen-plus").native_search.enabled is False
    # Text models must not be silently promoted to vision-capable models.
    assert all(
        model.capabilities.vision is False
        for name, model in config.models.items()
        if name != "qwen3-vl-plus"
    )
    assert config.models["qwen3-vl-plus"].capabilities.vision is True
    assert config.models["qwen3-vl-plus"].capabilities.structured_output is True
    assert all(
        model.capabilities.structured_output is False
        for name, model in config.models.items()
        if name != "qwen3-vl-plus"
    )


def test_qwen_adapter_translates_thinking_and_preserves_provider_metadata():
    adapter = QwenAdapter()
    model = adapter.build(
        provider_name="qwen",
        provider=ProviderConfig(
            adapter="qwen", base_url="https://example.test/v1", api_key_env="QWEN_KEY"
        ),
        model_name="qwen-max",
        model=ModelDefinition(
            provider="qwen", model_id="qwen3.8-max", context_window_tokens=1000,
            max_output_tokens=100, capabilities=ModelCapabilities(thinking=True),
        ),
        preset_name="qwen-max",
        preset=ModelPresetConfig(
            model="qwen-max",
            thinking=ThinkingConfig(enabled=True, effort="max"),
            generation=GenerationConfig(max_output_tokens=100),
        ),
        api_key="test",
    )

    payload = model._get_request_payload([
        HumanMessage(content="first question"),
        AIMessage(
            content="first answer",
            additional_kwargs={"reasoning_content": "历史分析"},
        ),
        HumanMessage(content="follow-up question"),
    ])
    assert payload["extra_body"] == {
        "enable_thinking": True,
        "preserve_thinking": True,
        "reasoning_effort": "xhigh",
    }
    assert payload["messages"][1]["reasoning_content"] == "历史分析"

    chunk = model._convert_chunk_to_generation_chunk(
        {
            "choices": [{
                "delta": {"role": "assistant", "content": "", "reasoning_content": "分析"},
                "finish_reason": None,
            }],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 3,
                "total_tokens": 7,
                "prompt_tokens_details": {"cached_tokens": 2},
                "completion_tokens_details": {"reasoning_tokens": 1},
            },
        },
        AIMessageChunk,
        None,
    )
    assert chunk is not None
    assert chunk.message.additional_kwargs["reasoning_content"] == "分析"
    assert chunk.message.usage_metadata["input_token_details"]["cache_read"] == 2
    assert chunk.message.usage_metadata["output_token_details"]["reasoning"] == 1

    result = model._create_chat_result({
        "choices": [{
            "message": {
                "role": "assistant", "content": "答案", "reasoning_content": "完整分析",
            },
            "finish_reason": "stop",
        }],
        "model": "qwen3.8-max",
        "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
    })
    assert result.generations[0].message.additional_kwargs["reasoning_content"] == "完整分析"
    assert result.generations[0].message.additional_kwargs["provider_usage"]["total_tokens"] == 7


def test_qwen_adapter_adds_search_only_for_an_opted_in_preset():
    model = QwenAdapter().build(
        provider_name="qwen",
        provider=ProviderConfig(
            adapter="qwen", base_url="https://example.test/v1", api_key_env="QWEN_KEY"
        ),
        model_name="qwen-plus",
        model=ModelDefinition(
            provider="qwen", model_id="qwen3.7-plus", context_window_tokens=1000,
            max_output_tokens=100, capabilities=ModelCapabilities(thinking=True),
        ),
        preset_name="qwen-web",
        preset=ModelPresetConfig(
            model="qwen-plus",
            generation=GenerationConfig(max_output_tokens=100),
            native_search=NativeSearchConfig(
                enabled=True, forced=True, strategy="turbo"
            ),
        ),
        api_key="test",
    )

    payload = model._get_request_payload([HumanMessage(content="今天有什么新闻？")])
    assert payload["extra_body"] == {
        "enable_thinking": False,
        "preserve_thinking": False,
        "enable_search": True,
        "search_options": {
            "forced_search": True,
            "search_strategy": "turbo",
        },
    }


def test_deepseek_usage_normalization_includes_kv_cache():
    usage = normalize_usage({
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "prompt_cache_hit_tokens": 75,
        "prompt_cache_miss_tokens": 25,
    })
    assert usage["input_token_details"]["cache_read"] == 75
    assert usage["input_token_details"]["cache_miss"] == 25


def test_deepseek_replays_reasoning_only_for_tool_call_messages():
    model = DeepSeekChatModel(
        model="deepseek-v4-pro", api_base="https://api.deepseek.com",
        api_key="test", max_retries=0,
    )
    plain = AIMessage(content="answer", additional_kwargs={"reasoning_content": "private"})
    tool = AIMessage(
        content="", additional_kwargs={"reasoning_content": "needed"},
        tool_calls=[{"id": "call-1", "name": "lookup", "args": {"q": "x"}, "type": "tool_call"}],
    )
    payload = model._get_request_payload([
        HumanMessage(content="one"), plain, HumanMessage(content="two"), tool,
    ])
    assert "reasoning_content" not in payload["messages"][1]
    assert payload["messages"][3]["reasoning_content"] == "needed"


@pytest.mark.asyncio
async def test_transient_errors_retry_then_fail_over():
    primary = FakeModel([StatusError(503), StatusError(503)])
    fallback = FakeModel([AIMessage(content="ok", usage_metadata={
        "input_tokens": 2, "output_tokens": 1, "total_tokens": 3,
    })])
    runtime = ResilientChatModel([
        candidate("primary", primary, attempts=2),
        candidate("fallback", fallback),
    ])
    result = await runtime.ainvoke([HumanMessage(content="hello")])
    assert result.content == "ok"
    assert primary.calls == 2
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_non_retryable_error_does_not_fail_over():
    primary = FakeModel([StatusError(400, "invalid tool schema")])
    fallback = FakeModel([AIMessage(content="must not run")])
    runtime = ResilientChatModel([candidate("primary", primary), candidate("fallback", fallback)])
    with pytest.raises(StatusError):
        await runtime.ainvoke([HumanMessage(content="hello")])
    assert fallback.calls == 0
    assert not classify_model_error(StatusError(400)).retryable


@pytest.mark.asyncio
async def test_stream_can_fail_over_before_first_delta():
    primary = FakeStreamModel([StatusError(503)])
    fallback = FakeStreamModel([AIMessage(content="ok")])
    runtime = ResilientChatModel([candidate("primary", primary), candidate("fallback", fallback)])
    chunks = [chunk async for chunk in runtime.astream([HumanMessage(content="hello")])]
    assert chunks[0].content == "ok"


@pytest.mark.asyncio
async def test_stream_never_replays_after_visible_delta():
    primary = FakeStreamModel([AIMessage(content="partial"), StatusError(503)])
    fallback = FakeStreamModel([AIMessage(content="duplicate")])
    runtime = ResilientChatModel([candidate("primary", primary), candidate("fallback", fallback)])
    stream = runtime.astream([HumanMessage(content="hello")])
    first = await anext(stream)
    assert first.content == "partial"
    with pytest.raises(StreamInterruptedError):
        await anext(stream)
    assert fallback.calls == 0


def test_bind_tools_applies_to_every_fallback_candidate():
    first, second = FakeModel([]), FakeModel([])
    runtime = ResilientChatModel([candidate("one", first), candidate("two", second)])
    bound = runtime.bind_tools(["tool"])
    assert first.bound_tools == ["tool"]
    assert second.bound_tools == ["tool"]
    assert len(bound.candidates) == 2
