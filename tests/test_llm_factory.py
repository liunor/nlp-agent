from pathlib import Path

import yaml
import pytest
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from core.model_runtime.contracts import ModelRuntimeConfig
from core.model_runtime.factory import ModelFactory
from core.model_runtime.selection import bind_model_profile
from core.skill_loader import SkillLoader
from server.agent import llm_factory
from server.tools import worker_tool


class RecordingFactory:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    @staticmethod
    def profile_preset(profile: str, role: str) -> str:
        assert role == "worker"
        return {
            "deepseek": "worker-flash",
            "qwen": "worker-qwen-plus",
        }[profile]

    def build_preset(self, name: str, *, model_profile: str | None = None):
        self.calls.append(("preset", name))
        return ("preset", name)

    def build_profile_role(self, profile: str, role: str):
        preset = self.profile_preset(profile, role)
        self.calls.append(("profile_role", profile, role, preset))
        return ("preset", preset)

    def build_route(self, name: str, *, model_profile: str | None = None):
        self.calls.append(("route", name, model_profile))
        return ("route", name)

    def build_override(self, name: str, *, base_route: str = "worker", model_profile: str | None = None):
        assert base_route == "worker"
        self.calls.append(("override", name, model_profile))
        return ("override", name)


def test_explicit_worker_preset_wins_over_turn_model_profile(monkeypatch):
    factory = RecordingFactory()
    monkeypatch.setattr(llm_factory, "get_global_model_factory", lambda: factory)
    monkeypatch.setattr(llm_factory.settings, "NLP_AGENT_WORKER_MODEL", "")

    with bind_model_profile("deepseek"):
        assert (
            llm_factory.resolve_worker_model_name(
                agent_name="web_researcher",
                requested_model="worker-qwen-web",
            )
            == "worker-qwen-web"
        )
        assert llm_factory.get_worker_llm(tool_specified_model="worker-qwen-web") == (
            "override",
            "worker-qwen-web",
        )

    assert factory.calls == [("override", "worker-qwen-web", None)]


def test_turn_model_profile_is_used_when_worker_has_no_override(monkeypatch):
    factory = RecordingFactory()
    monkeypatch.setattr(llm_factory, "get_global_model_factory", lambda: factory)
    monkeypatch.setattr(llm_factory.settings, "NLP_AGENT_WORKER_MODEL", "")

    with bind_model_profile("qwen"):
        assert llm_factory.resolve_worker_model_name() == "worker-qwen-plus"
        assert llm_factory.get_worker_llm() == ("preset", "worker-qwen-plus")

    assert factory.calls == [("profile_role", "qwen", "worker", "worker-qwen-plus")]


def test_real_factory_builds_the_dedicated_qwen_web_preset(monkeypatch):
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
    factory = ModelFactory(config)
    monkeypatch.setattr(factory, "_api_key", lambda _env_name: "test")
    monkeypatch.setattr(llm_factory, "get_global_model_factory", lambda: factory)
    monkeypatch.setattr(llm_factory.settings, "NLP_AGENT_WORKER_MODEL", "")

    with bind_model_profile("deepseek"):
        runtime = llm_factory.get_worker_llm(
            tool_specified_model="worker-qwen-web"
        )

    assert len(runtime.candidates) == 1
    candidate = runtime.candidates[0]
    assert candidate.preset_name == "worker-qwen-web"
    assert candidate.provider_name == "qwen"
    assert candidate.preset.native_search.enabled is True
    payload = candidate.model._get_request_payload(
        [HumanMessage(content="今天有什么新闻？")]
    )
    assert payload["extra_body"]["enable_search"] is True
    assert payload["extra_body"]["search_options"] == {
        "forced_search": True,
        "search_strategy": "turbo",
    }


def test_native_search_profile_model_is_locked_and_exclusive(monkeypatch):
    monkeypatch.setattr(llm_factory.settings, "NLP_AGENT_WORKER_MODEL", "")
    loader = SkillLoader()
    researcher = loader.resolve_profile("web_researcher")
    reader = loader.resolve_profile("web_reader")

    assert worker_tool._resolve_profile_worker_model(
        researcher, model_profile="deepseek"
    ) == "worker-qwen-web"
    with pytest.raises(ValueError, match="pins model 'worker-qwen-web'"):
        worker_tool._resolve_profile_worker_model(
            researcher, requested_model="worker-flash", model_profile="deepseek"
        )
    with pytest.raises(ValueError, match="not authorized to use native-search"):
        worker_tool._resolve_profile_worker_model(
            reader, requested_model="worker-qwen-web", model_profile="deepseek"
        )


def test_native_search_profile_rejects_a_conflicting_global_override(monkeypatch):
    profile = SkillLoader().resolve_profile("web_researcher")
    monkeypatch.setattr(
        llm_factory.settings, "NLP_AGENT_WORKER_MODEL", "worker-flash"
    )

    with pytest.raises(ValueError, match="global Worker override"):
        worker_tool._resolve_profile_worker_model(profile, model_profile="deepseek")


def test_native_search_profile_forces_one_shot_execution_policy():
    profile = SkillLoader().resolve_profile("web_researcher")
    budget, retry = worker_tool._build_profile_execution_policies(
        profile,
        runtime_settings={
            "max_injections": 15,
            "injection_batch_size": 3,
            "max_tool_result_chars": 50_000,
            "finalize_on_exhaustion": True,
        },
        max_turns=6,
        max_duration_s=60,
        max_tokens=32_000,
        max_tool_calls=12,
        max_attempts=3,
    )

    assert budget.max_turns == 1
    assert budget.max_tool_calls == 0
    assert budget.max_injections == 0
    assert budget.finalize_on_exhaustion is False
    assert retry.max_attempts == 1


def test_global_worker_override_remains_highest_priority(monkeypatch):
    factory = RecordingFactory()
    monkeypatch.setattr(llm_factory, "get_global_model_factory", lambda: factory)
    monkeypatch.setattr(
        llm_factory.settings, "NLP_AGENT_WORKER_MODEL", "worker-pro"
    )

    with bind_model_profile("qwen"):
        assert (
            llm_factory.resolve_worker_model_name(
                requested_model="worker-qwen-web"
            )
            == "worker-pro"
        )


def test_model_factory_profile_identity_and_estimation(monkeypatch):
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
    factory = ModelFactory(config)
    monkeypatch.setattr(factory, "_api_key", lambda _env_name: "test")

    # 1. Profile identity checks
    deepseek_id = factory.profile_identity("deepseek", "coordinator")
    assert deepseek_id.provider == "deepseek"
    assert deepseek_id.provider_model == "deepseek-v4-pro"
    assert deepseek_id.model_profile == "deepseek"
    assert deepseek_id.pricing_key == "deepseek/deepseek-v4-pro"

    qwen_id = factory.profile_identity("qwen", "coordinator")
    assert qwen_id.provider == "qwen"
    assert qwen_id.provider_model == "qwen3.8-max"
    assert qwen_id.model_profile == "qwen"
    assert qwen_id.pricing_key == "qwen/qwen3.8-max"

    # 2. Token estimation check
    est = factory.estimate_input_tokens("deepseek", [HumanMessage(content="test message")])
    assert est is not None
    assert est > 0
    with pytest.raises(KeyError, match="Unknown model profile"):
        factory.estimate_input_tokens("missing", [HumanMessage(content="test message")])

    # 3. ModelProfile is preserved on runtime and wrappers
    model = factory.build_profile_role("deepseek", "coordinator")
    assert model.model_profile == "deepseek"
    assert model.reporter_slot is factory.reporter_slot

    bound = model.bind_tools([])
    assert bound.model_profile == "deepseek"
    assert bound.reporter_slot is factory.reporter_slot

    class SampleSchema(BaseModel):
        name: str

    structured = model.with_structured_output(SampleSchema)
    assert structured.model_profile == "deepseek"
    assert structured.reporter_slot is factory.reporter_slot

    vision_route = factory.build_route("vision-worker")
    assert vision_route.model_profile is None
    assert vision_route.route == "vision-worker"
