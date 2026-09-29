from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from core.model_runtime.adapters.glm import GLMAdapter, GLMChatModel
from core.model_runtime.contracts import (
    CircuitBreakerPolicy,
    GenerationConfig,
    ModelCapabilities,
    ModelDefinition,
    ModelPresetConfig,
    ModelRuntimeConfig,
    ProviderConfig,
    RetryPolicy,
    ThinkingConfig,
    TimeoutPolicy,
)
from core.model_runtime.factory import ModelFactory
from core.model_runtime.reporters import (
    InMemoryModelUsageReporter,
    ModelUsageReporterSlot,
)
from core.model_runtime.runtime import (
    ModelCandidate,
    ModelFinishReasonError,
    ResilientChatModel,
    StreamInterruptedError,
    classify_model_error,
)
from core.model_runtime.usage import UsageAttributionContext, bind_usage_attribution


def _project_config() -> tuple[dict, ModelRuntimeConfig]:
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
    return raw, config


def _build_glm(
    *,
    model_id: str = "glm-5.3",
    thinking_enabled: bool = True,
    effort: str = "high",
    temperature: float | None = None,
) -> GLMChatModel:
    return GLMAdapter().build(
        provider_name="glm",
        provider=ProviderConfig(
            adapter="glm",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            api_key_env="GLM_API_KEY",
        ),
        model_name=model_id,
        model=ModelDefinition(
            provider="glm",
            model_id=model_id,
            pricing_key=f"glm/{model_id}",
            context_window_tokens=1_048_576,
            max_output_tokens=131_072,
            capabilities=ModelCapabilities(thinking=True, cache_usage=True),
        ),
        preset_name=f"test-{model_id}",
        preset=ModelPresetConfig(
            model=model_id,
            thinking=ThinkingConfig(
                enabled=thinking_enabled,
                effort=effort if thinking_enabled else "none",
            ),
            generation=GenerationConfig(
                max_output_tokens=16_000,
                temperature=temperature,
            ),
        ),
        api_key="test",
    )


def _runtime_preset(*, attempts: int = 1) -> ModelPresetConfig:
    return ModelPresetConfig(
        model="glm-test",
        thinking=ThinkingConfig(enabled=True, effort="high"),
        generation=GenerationConfig(max_output_tokens=100),
        timeouts=TimeoutPolicy(
            connect_s=1,
            first_token_s=1,
            stream_idle_s=1,
            total_s=2,
        ),
        retry=RetryPolicy(
            max_attempts=attempts,
            base_delay_s=0,
            max_delay_s=0,
            jitter="none",
        ),
        circuit_breaker=CircuitBreakerPolicy(
            failure_threshold=10,
            cooldown_s=1,
        ),
    )


def _runtime_candidate(
    model: object,
    *,
    name: str = "glm-test",
    attempts: int = 1,
) -> ModelCandidate:
    return ModelCandidate(
        preset_name=name,
        provider_name="glm",
        model_name=name,
        definition=ModelDefinition(
            provider="glm",
            model_id=name,
            pricing_key=f"glm/{name}",
            context_window_tokens=1_000,
            max_output_tokens=100,
            capabilities=ModelCapabilities(thinking=True),
        ),
        preset=_runtime_preset(attempts=attempts),
        model=model,
    )


def _attribution() -> UsageAttributionContext:
    return UsageAttributionContext(
        request_id="req-glm",
        user_id="user-glm",
        workspace_id="workspace-glm",
        conversation_id="conversation-glm",
        turn_id="turn-glm",
        purpose="worker",
    )


class FakeGLMModel:
    ERROR_FINISH_REASONS = GLMChatModel.ERROR_FINISH_REASONS

    def __init__(
        self,
        *,
        streams: list[list[AIMessageChunk]] | None = None,
        responses: list[AIMessage] | None = None,
    ) -> None:
        self.streams = list(streams or [])
        self.responses = list(responses or [])
        self.calls = 0

    async def astream(self, _input, config=None, **_kwargs):
        del config
        self.calls += 1
        for chunk in self.streams.pop(0):
            yield chunk

    async def ainvoke(self, _input, config=None, **_kwargs):
        del config
        self.calls += 1
        return self.responses.pop(0)

    def bind_tools(self, _tools, **_kwargs):
        return SimpleNamespace()

    def with_structured_output(self, _schema, **_kwargs):
        return SimpleNamespace()


class GLMAPIError(RuntimeError):
    def __init__(
        self,
        code: int | str,
        status_code: int,
        *,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(f"GLM request failed with code {code}")
        self.status_code = status_code
        self.body = {"error": {"code": code, "message": "provider error"}}
        self.response = SimpleNamespace(
            headers={"Retry-After": retry_after} if retry_after else {}
        )


def test_project_glm_profile_is_valid_without_changing_defaults_or_routes():
    raw, config = _project_config()

    assert raw["defaults"] == {
        "coordinator": "coordinator-pro",
        "worker": "worker-flash",
        "model_profile": "deepseek",
    }
    assert config.providers["glm"] == ProviderConfig(
        adapter="glm",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key_env="GLM_API_KEY",
    )
    for name in ("glm-5.3", "glm-5.2"):
        model = config.models[name]
        assert model.model_id == name
        assert model.pricing_key == f"glm/{name}"
        assert model.context_window_tokens == 1_048_576
        assert model.max_output_tokens == 131_072
        assert model.capabilities.thinking is True
        assert model.capabilities.cache_usage is True
        assert model.capabilities.vision is False

    coordinator = config.preset("coordinator-glm")
    worker = config.preset("worker-glm")
    utility = config.preset("utility-glm")
    assert coordinator.model == "glm-5.3"
    assert coordinator.thinking.effort.value == "max"
    assert worker.model == "glm-5.3"
    assert worker.thinking.effort.value == "high"
    assert utility.model == "glm-5.2"
    assert utility.thinking.enabled is False
    assert utility.generation.temperature == 0.1

    profile = config.profile("glm")
    assert profile.provider == "glm"
    assert profile.coordinator == "coordinator-glm"
    assert profile.worker == "worker-glm"
    assert profile.utility == "utility-glm"
    assert config.model_routes["coordinator"].primary == "coordinator-pro"
    assert config.model_routes["worker"].primary == "worker-flash"
    assert config.model_routes["utility"].primary == "utility-flash"
    assert config.model_routes["vision-worker"].primary == "vision-qwen-plus"
    assert raw["worker_profiles"]["web_researcher"]["model"] == "worker-qwen-web"


@pytest.mark.parametrize(
    ("effort", "expected"),
    (("low", "low"), ("medium", "high"), ("high", "high"), ("max", "max")),
)
def test_glm_53_effort_mapping_and_tool_stream(effort: str, expected: str):
    model = _build_glm(effort=effort)
    payload = model._get_request_payload([HumanMessage(content="hello")])

    assert payload["reasoning_effort"] == expected
    assert payload["extra_body"] == {
        "thinking": {"type": "enabled"},
        "tool_stream": True,
    }


def test_glm_53_rejects_disabled_thinking():
    with pytest.raises(ValueError, match="cannot disable thinking"):
        _build_glm(thinking_enabled=False)


def test_glm_52_can_disable_thinking_and_use_temperature():
    model = _build_glm(
        model_id="glm-5.2",
        thinking_enabled=False,
        temperature=0.1,
    )
    payload = model._get_request_payload([HumanMessage(content="hello")])

    assert payload["reasoning_effort"] == "none"
    assert payload["temperature"] == 0.1
    assert payload["extra_body"] == {
        "thinking": {"type": "disabled"},
        "tool_stream": True,
    }


def test_glm_replays_reasoning_only_for_tool_call_messages():
    model = _build_glm()
    plain = AIMessage(
        content="answer",
        additional_kwargs={"reasoning_content": "private"},
    )
    tool = AIMessage(
        content="",
        additional_kwargs={"reasoning_content": "needed"},
        tool_calls=[
            {
                "id": "call-1",
                "name": "lookup",
                "args": {"q": "x"},
                "type": "tool_call",
            }
        ],
    )

    payload = model._get_request_payload(
        [HumanMessage(content="one"), plain, HumanMessage(content="two"), tool]
    )

    assert "reasoning_content" not in payload["messages"][1]
    assert payload["messages"][3]["reasoning_content"] == "needed"


def test_glm_preserves_reasoning_response_id_and_nested_cached_usage():
    model = _build_glm()
    chunk = model._convert_chunk_to_generation_chunk(
        {
            "id": "glm-chunk-1",
            "choices": [
                {
                    "delta": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "分析",
                    },
                    "finish_reason": None,
                }
            ],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 3,
                "total_tokens": 7,
                "prompt_tokens_details": {"cached_tokens": 2},
            },
        },
        AIMessageChunk,
        None,
    )
    assert chunk is not None
    assert chunk.message.additional_kwargs["reasoning_content"] == "分析"
    assert chunk.message.additional_kwargs["provider_response_id"] == "glm-chunk-1"
    assert chunk.message.usage_metadata["input_token_details"]["cache_read"] == 2

    result = model._create_chat_result(
        {
            "id": "glm-response-1",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "答案",
                        "reasoning_content": "完整分析",
                    },
                    "finish_reason": "stop",
                }
            ],
            "model": "glm-5.3",
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 3,
                "total_tokens": 7,
                "prompt_tokens_details": {"cached_tokens": 2},
            },
        }
    )
    message = result.generations[0].message
    assert message.additional_kwargs["reasoning_content"] == "完整分析"
    assert message.additional_kwargs["provider_response_id"] == "glm-response-1"
    assert message.additional_kwargs["provider_usage"]["prompt_cache_hit_tokens"] == 2


def test_factory_registers_glm_and_retains_finish_reason_contract(monkeypatch):
    _, config = _project_config()
    factory = ModelFactory(config)
    monkeypatch.setattr(factory, "_api_key", lambda _env_name: "test")

    runtime = factory.build_profile_role("glm", "worker")

    assert len(runtime.candidates) == 1
    candidate = runtime.candidates[0]
    assert candidate.provider_name == "glm"
    assert candidate.preset_name == "worker-glm"
    assert isinstance(candidate.model, GLMChatModel)
    assert candidate.error_finish_reasons == GLMChatModel.ERROR_FINISH_REASONS


def test_finish_reason_contract_survives_runtime_wrappers():
    candidate = _runtime_candidate(FakeGLMModel())
    runtime = ResilientChatModel([candidate])

    bound = runtime.bind_tools([])
    structured = runtime.with_structured_output(dict)

    assert bound.candidates[0].error_finish_reasons == GLMChatModel.ERROR_FINISH_REASONS
    assert (
        structured.candidates[0].error_finish_reasons
        == GLMChatModel.ERROR_FINISH_REASONS
    )


@pytest.mark.parametrize(
    ("code", "status", "kind", "retryable"),
    (
        (1113, 429, "upstream_provider_quota_exhausted", False),
        ("1261", 400, "upstream_context_length_exceeded", False),
        (1301, 400, "upstream_unknown", False),
        (1210, 400, "upstream_invalid_request", False),
        (1212, 400, "upstream_invalid_request", False),
        (1213, 400, "upstream_invalid_request", False),
        (1214, 400, "upstream_invalid_request", False),
        (1215, 400, "upstream_invalid_request", False),
        (1211, 400, "upstream_model_unavailable", False),
        (1221, 400, "upstream_model_unavailable", False),
        (1222, 400, "upstream_model_unavailable", False),
        (1302, 429, "upstream_rate_limited", True),
        (1305, 429, "upstream_overloaded", True),
        (1308, 429, "upstream_provider_quota_exhausted", False),
        (1309, 429, "upstream_provider_quota_exhausted", False),
        (1310, 429, "upstream_provider_quota_exhausted", False),
        (1311, 429, "upstream_provider_quota_exhausted", False),
        (1313, 429, "upstream_provider_quota_exhausted", False),
        (1314, 429, "upstream_provider_quota_exhausted", False),
        (1315, 429, "upstream_provider_quota_exhausted", False),
        (1316, 429, "upstream_provider_quota_exhausted", False),
        (1317, 429, "upstream_provider_quota_exhausted", False),
        (1318, 429, "upstream_provider_quota_exhausted", False),
        (1319, 429, "upstream_provider_quota_exhausted", False),
        (1320, 429, "upstream_provider_quota_exhausted", False),
        (1321, 429, "upstream_provider_quota_exhausted", False),
    ),
)
def test_glm_numeric_error_codes_precede_generic_http_classification(
    code: int | str,
    status: int,
    kind: str,
    retryable: bool,
):
    decision = classify_model_error(GLMAPIError(code, status, retry_after="2.5"))

    assert decision.kind == kind
    assert decision.retryable is retryable
    if retryable:
        assert decision.retry_after_s == 2.5


def test_undocumented_glm_code_keeps_generic_http_behavior():
    decision = classify_model_error(GLMAPIError(1306, 429))

    assert decision.kind == "upstream_rate_limited"
    assert decision.retryable is True


@pytest.mark.asyncio
async def test_no_visible_network_finish_reason_retries_then_succeeds():
    model = FakeGLMModel(
        streams=[
            [
                AIMessageChunk(
                    content="",
                    response_metadata={"finish_reason": "network_error"},
                )
            ],
            [
                AIMessageChunk(
                    content="ok",
                    response_metadata={"finish_reason": "stop"},
                )
            ],
        ]
    )
    runtime = ResilientChatModel([_runtime_candidate(model, attempts=2)])

    chunks = [chunk async for chunk in runtime.astream([HumanMessage(content="hello")])]

    assert "".join(str(chunk.content) for chunk in chunks) == "ok"
    assert model.calls == 2


@pytest.mark.asyncio
async def test_visible_network_finish_reason_interrupts_without_fallback():
    primary = FakeGLMModel(
        streams=[
            [
                AIMessageChunk(content="partial"),
                AIMessageChunk(
                    content="",
                    response_metadata={"finish_reason": "network_error"},
                ),
            ]
        ]
    )
    fallback = FakeGLMModel(streams=[[AIMessageChunk(content="must not run")]])
    runtime = ResilientChatModel(
        [
            _runtime_candidate(primary, name="primary"),
            _runtime_candidate(fallback, name="fallback"),
        ]
    )
    stream = runtime.astream([HumanMessage(content="hello")])

    assert (await anext(stream)).content == "partial"
    assert (await anext(stream)).content == ""
    with pytest.raises(StreamInterruptedError):
        await anext(stream)
    assert fallback.calls == 0


@pytest.mark.parametrize(
    ("finish_reason", "kind"),
    (
        ("model_context_window_exceeded", "upstream_context_length_exceeded"),
        ("sensitive", "upstream_unknown"),
    ),
)
@pytest.mark.asyncio
async def test_non_retryable_finish_reason_does_not_retry(
    finish_reason: str,
    kind: str,
):
    model = FakeGLMModel(
        streams=[
            [
                AIMessageChunk(
                    content="", response_metadata={"finish_reason": finish_reason}
                )
            ]
        ]
    )
    runtime = ResilientChatModel([_runtime_candidate(model, attempts=2)])

    with pytest.raises(ModelFinishReasonError) as captured:
        async for _chunk in runtime.astream([HumanMessage(content="hello")]):
            pass

    assert captured.value.error_kind == kind
    assert captured.value.finish_reason == finish_reason
    assert model.calls == 1


@pytest.mark.asyncio
async def test_direct_finish_reason_retries_and_is_reported_with_usage():
    reporter = InMemoryModelUsageReporter()
    model = FakeGLMModel(
        responses=[
            AIMessage(
                content="",
                response_metadata={
                    "finish_reason": "network_error",
                    "token_usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 1,
                    },
                },
            ),
            AIMessage(
                content="ok",
                response_metadata={
                    "finish_reason": "stop",
                    "token_usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 2,
                    },
                },
            ),
        ]
    )
    runtime = ResilientChatModel(
        [_runtime_candidate(model, attempts=2)],
        normalize_response=False,
        reporter_slot=ModelUsageReporterSlot(reporter),
    )

    with bind_usage_attribution(_attribution()):
        result = await runtime.ainvoke([HumanMessage(content="hello")])

    assert result.content == "ok"
    assert model.calls == 2
    assert len(reporter.events) == 2
    _, first_usage, first_outcome = reporter.events[0]
    assert first_usage.total_tokens == 6
    assert first_outcome.status == "failed"
    assert first_outcome.finish_reason == "network_error"
    assert first_outcome.error_kind == "upstream_overloaded"
    assert reporter.events[1][2].status == "succeeded"
    assert reporter.events[1][2].finish_reason == "stop"
