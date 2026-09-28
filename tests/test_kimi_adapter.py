from pathlib import Path

import pytest
import yaml
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from core.model_runtime.adapters.kimi import KimiAdapter, KimiChatModel
from core.model_runtime.contracts import (
    GenerationConfig,
    ModelCapabilities,
    ModelDefinition,
    ModelPresetConfig,
    ModelRuntimeConfig,
    ProviderConfig,
    ThinkingConfig,
)
from core.model_runtime.factory import ModelFactory


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


def _build_kimi(
    *,
    thinking_enabled: bool = True,
    effort: str = "high",
    generation: GenerationConfig | None = None,
) -> KimiChatModel:
    return KimiAdapter().build(
        provider_name="kimi",
        provider=ProviderConfig(
            adapter="kimi",
            base_url="https://api.moonshot.cn/v1",
            api_key_env="KIMI_API_KEY",
        ),
        model_name="kimi-k2.6",
        model=ModelDefinition(
            provider="kimi",
            model_id="kimi-k2.6",
            pricing_key="kimi/kimi-k2.6",
            context_window_tokens=262_144,
            max_output_tokens=32_768,
            capabilities=ModelCapabilities(thinking=True, cache_usage=True),
        ),
        preset_name="test-kimi",
        preset=ModelPresetConfig(
            model="kimi-k2.6",
            thinking=ThinkingConfig(
                enabled=thinking_enabled,
                effort=effort if thinking_enabled else "none",
            ),
            generation=generation
            or GenerationConfig(max_output_tokens=16_000),
        ),
        api_key="test",
    )


def test_project_kimi_profile_is_valid_without_changing_default_routes():
    raw, config = _project_config()

    assert raw["defaults"] == {
        "coordinator": "coordinator-pro",
        "worker": "worker-flash",
        "model_profile": "deepseek",
    }
    assert config.providers["kimi"] == ProviderConfig(
        adapter="kimi",
        base_url="https://api.moonshot.cn/v1",
        api_key_env="KIMI_API_KEY",
    )
    model = config.models["kimi-k2.6"]
    assert model.model_id == "kimi-k2.6"
    assert model.pricing_key == "kimi/kimi-k2.6"
    assert model.context_window_tokens == 262_144
    assert model.max_output_tokens == 32_768
    assert model.capabilities.thinking is True
    assert model.capabilities.cache_usage is True
    assert model.capabilities.vision is False

    coordinator = config.preset("coordinator-kimi")
    worker = config.preset("worker-kimi")
    utility = config.preset("utility-kimi")
    assert coordinator.thinking.enabled is True
    assert worker.thinking.enabled is True
    assert utility.thinking.enabled is False
    assert coordinator.generation.max_output_tokens >= 16_000
    assert worker.generation.max_output_tokens >= 16_000
    for kimi_preset in (coordinator, worker, utility):
        assert kimi_preset.generation.temperature is None
        assert kimi_preset.generation.top_p is None

    profile = config.profile("kimi")
    assert profile.provider == "kimi"
    assert profile.coordinator == "coordinator-kimi"
    assert profile.worker == "worker-kimi"
    assert profile.utility == "utility-kimi"
    assert config.model_routes["coordinator"].primary == "coordinator-pro"
    assert config.model_routes["worker"].primary == "worker-flash"
    assert config.model_routes["utility"].primary == "utility-flash"


@pytest.mark.parametrize(
    ("thinking_enabled", "expected_type"),
    ((True, "enabled"), (False, "disabled")),
)
def test_kimi_payload_uses_thinking_structure_without_unsupported_parameters(
    thinking_enabled: bool,
    expected_type: str,
):
    model = _build_kimi(thinking_enabled=thinking_enabled)
    payload = model._get_request_payload([HumanMessage(content="hello")])

    assert payload["extra_body"] == {
        "thinking": {"type": expected_type, "keep": None}
    }
    assert payload["max_completion_tokens"] == 16_000
    for unsupported in ("temperature", "top_p", "n", "effort", "reasoning_effort"):
        assert unsupported not in payload


@pytest.mark.parametrize(
    ("generation", "field"),
    (
        (GenerationConfig(max_output_tokens=16_000, temperature=0.1), "temperature"),
        (GenerationConfig(max_output_tokens=16_000, top_p=0.8), "top_p"),
    ),
)
def test_kimi_adapter_rejects_sampling_parameters(
    generation: GenerationConfig,
    field: str,
):
    with pytest.raises(ValueError, match=field):
        _build_kimi(generation=generation)


def test_kimi_replays_reasoning_only_for_tool_call_messages():
    model = _build_kimi()
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


def test_kimi_preserves_reasoning_response_id_and_cached_usage():
    model = _build_kimi()
    chunk = model._convert_chunk_to_generation_chunk(
        {
            "id": "kimi-chunk-1",
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
                "cached_tokens": 2,
            },
        },
        AIMessageChunk,
        None,
    )
    assert chunk is not None
    assert chunk.message.additional_kwargs["reasoning_content"] == "分析"
    assert chunk.message.additional_kwargs["provider_response_id"] == "kimi-chunk-1"
    assert chunk.message.usage_metadata["input_token_details"]["cache_read"] == 2

    result = model._create_chat_result(
        {
            "id": "kimi-response-1",
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
            "model": "kimi-k2.6",
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 3,
                "total_tokens": 7,
                "cached_tokens": 2,
            },
        }
    )
    message = result.generations[0].message
    assert message.additional_kwargs["reasoning_content"] == "完整分析"
    assert message.additional_kwargs["provider_response_id"] == "kimi-response-1"
    assert message.additional_kwargs["provider_usage"]["prompt_cache_hit_tokens"] == 2


def test_factory_registers_and_builds_the_kimi_profile(monkeypatch):
    _, config = _project_config()
    factory = ModelFactory(config)
    monkeypatch.setattr(factory, "_api_key", lambda _env_name: "test")

    runtime = factory.build_profile_role("kimi", "worker")

    assert len(runtime.candidates) == 1
    candidate = runtime.candidates[0]
    assert candidate.provider_name == "kimi"
    assert candidate.preset_name == "worker-kimi"
    assert isinstance(candidate.model, KimiChatModel)
