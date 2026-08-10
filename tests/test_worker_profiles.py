from pathlib import Path

import pytest

from core.skill_loader import SkillLoader
from core.tool_config import load_agent_runtime_config


def test_nlp_calculator_profile_is_a_valid_worker_with_all_nlp_tools():
    root = Path(__file__).resolve().parents[1]
    config = load_agent_runtime_config(root / "configs" / "agent_config.yaml")
    profile = config.worker_profiles["nlp_calculator"]

    assert profile.capabilities == {"nlp.analyze"}
    assert profile.allowed_tools == {
        "nlp_tfidf_analyzer", "nlp_precision_recall_curve", "nlp_precision_at_n",
        "nlp_ngram_analyzer", "nlp_bleu_score",
    }
    resolved = SkillLoader().resolve_profile("nlp_calculator")
    assert resolved.name == "nlp_calculator"
    assert "nlp_bleu_score" in resolved.allowed_tools


def test_coordinator_prompt_teaches_general_dependency_aware_worker_routing():
    root = Path(__file__).resolve().parents[1]
    prompt = (root / "core" / "prompt_runtime" / "templates" / "coordinator.v1.2.md").read_text(encoding="utf-8")

    assert "并发和并行是你可主动使用的能力" in prompt
    assert "先列出可独立交付的产物" in prompt
    assert "可独立交付的产物达到两个" in prompt
    assert "后续任务只在其依赖的前序产物已可用后启动" in prompt
    assert "不得向用户声称仍在等待" in prompt
    assert "不得在最终答复中输出 [INTERNAL_WORKER_RESULTS]" in prompt


def test_web_researcher_profile_grants_web_search_and_fetch():
    root = Path(__file__).resolve().parents[1]
    config = load_agent_runtime_config(root / "configs" / "agent_config.yaml")
    profile = config.worker_profiles["web_researcher"]

    assert profile.capabilities == {"web.search", "web.fetch"}
    assert profile.allowed_tools == {"web_search", "web_fetch"}
    resolved = SkillLoader().resolve_profile("web_researcher")
    assert resolved.name == "web_researcher"
    assert {"web_search", "web_fetch"} <= resolved.allowed_tools


def test_worker_prompt_v1_3_teaches_web_tool_discipline():
    root = Path(__file__).resolve().parents[1]
    prompt = (root / "core" / "prompt_runtime" / "templates" / "worker.v1.3.md").read_text(encoding="utf-8")

    assert "联网检索纪律" in prompt
    assert "一律不得执行" in prompt
    assert "标题 — URL" in prompt
    assert "不得编造网页内容" in prompt


def test_pinned_worker_prompt_version_template_exists():
    import yaml

    from core.prompt_runtime.registry import PromptRegistry

    root = Path(__file__).resolve().parents[1]
    raw = yaml.safe_load(
        (root / "configs" / "agent_config.yaml").read_text(encoding="utf-8")
    )
    versions = raw["prompts"]["versions"]
    registry = PromptRegistry(versions=versions)
    spec, template = registry.load("worker")
    assert spec.version == versions["worker"]
    assert "{{today}}" in template


@pytest.mark.asyncio
async def test_runtime_reload_refreshes_coordinator_worker_profile_listing(monkeypatch):
    from server.agent.node import coordinator
    from server.web import developer_runtime

    original_cached = coordinator._CACHED_SYSTEM_MESSAGE
    listings = iter(("- before-profile: old", "- after-profile: new"))
    monkeypatch.setattr(coordinator.skill_loader, "get_planner_listing", lambda: next(listings))
    monkeypatch.setattr(
        coordinator.global_prompt_runtime,
        "render",
        lambda _key, *, worker_profiles: worker_profiles,
    )
    coordinator._CACHED_SYSTEM_MESSAGE = None
    try:
        assert "before-profile" in coordinator._get_system_message().content

        await developer_runtime.reload_runtime(reload_skills=True)

        assert "after-profile" in coordinator._get_system_message().content
    finally:
        coordinator._CACHED_SYSTEM_MESSAGE = original_cached
