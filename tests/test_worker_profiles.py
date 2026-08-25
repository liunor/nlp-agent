from pathlib import Path

import pytest

from core.skill_loader import SkillLoader
from core.tool_config import VisionToolsConfig, load_agent_runtime_config


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


@pytest.mark.asyncio
async def test_nlp_calculator_keeps_its_explicit_tools_without_global_worker_grants():
    from core.tool_registry import physical_tool_manager

    physical_tool_manager.refresh_config()
    before = set(physical_tool_manager.runtime.catalog.names())
    extensions_were_loaded = physical_tool_manager._extensions_loaded
    try:
        await physical_tool_manager.start_extensions()
        profile = SkillLoader().resolve_profile("nlp_calculator")
        toolset = physical_tool_manager.get_worker_toolset(
            allowed_names=profile.allowed_tools,
            capabilities=profile.capabilities,
            profile=profile.name,
        )

        assert set(toolset.names) == {
            "nlp_tfidf_analyzer",
            "nlp_precision_recall_curve",
            "nlp_precision_at_n",
            "nlp_ngram_analyzer",
            "nlp_bleu_score",
        }
    finally:
        if not extensions_were_loaded:
            for name in set(physical_tool_manager.runtime.catalog.names()) - before:
                physical_tool_manager.runtime.catalog.unregister(name)
            physical_tool_manager._extensions_loaded = False


def test_coordinator_prompt_teaches_general_dependency_aware_worker_routing():
    root = Path(__file__).resolve().parents[1]
    prompt = (root / "core" / "prompt_runtime" / "templates" / "coordinator.v1.3.md").read_text(encoding="utf-8")

    assert "并发和并行是你可主动使用的能力" in prompt
    assert "先列出可独立交付的产物" in prompt
    assert "可独立交付的产物达到两个" in prompt
    assert "后续任务只在其依赖的前序产物已可用后启动" in prompt
    assert "不得向用户声称仍在等待" in prompt
    assert "不得在最终答复中输出 [INTERNAL_WORKER_RESULTS]" in prompt


def test_web_profiles_separate_native_search_from_explicit_url_reads():
    import yaml

    root = Path(__file__).resolve().parents[1]
    config_path = root / "configs" / "agent_config.yaml"
    config = load_agent_runtime_config(config_path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    researcher = config.worker_profiles["web_researcher"]
    reader = config.worker_profiles["web_reader"]

    assert researcher.model == "worker-qwen-web"
    assert researcher.execution_mode == "one_shot"
    assert researcher.requires_native_search is True
    assert researcher.inherit_tool_policy is False
    assert researcher.capabilities == set()
    assert researcher.allowed_tools == set()
    assert reader.model is None
    assert reader.execution_mode == "react"
    assert reader.requires_native_search is False
    assert reader.inherit_tool_policy is False
    assert reader.capabilities == {"web.fetch"}
    assert reader.allowed_tools == {"web_fetch"}

    loader = SkillLoader()
    assert loader.resolve_profile("web_researcher").model == "worker-qwen-web"
    assert loader.resolve_profile("web_reader").allowed_tools == {"web_fetch"}
    assert raw["model_presets"]["worker-qwen-web"]["native_search"] == {
        "enabled": True,
        "forced": True,
        "strategy": "turbo",
    }
    assert "search" not in raw["tools"]["web"]
    assert config.tools.policies.worker.allowed_capabilities == set()


def test_web_profiles_receive_only_their_declared_tools():
    from core.tool_config import RoleToolPolicy
    from core.tool_registry import physical_tool_manager

    physical_tool_manager.refresh_config()
    original_config = physical_tool_manager.config
    policies = original_config.tools.policies.model_copy(
        update={
            "worker": RoleToolPolicy(
                allowed_tools={"get_current_time"},
                allowed_capabilities={"nlp.analyze"},
                denied_capabilities={"runtime.control", "worker.manage"},
            )
        }
    )
    physical_tool_manager.config = original_config.model_copy(
        update={
            "tools": original_config.tools.model_copy(
                update={"policies": policies}
            )
        }
    )
    loader = SkillLoader()
    try:
        researcher = loader.resolve_profile("web_researcher")
        researcher_tools = physical_tool_manager.get_worker_toolset(
            allowed_names=researcher.allowed_tools,
            capabilities=researcher.capabilities,
            profile=researcher.name,
            inherit_policy=researcher.inherit_tool_policy,
        )
        reader = loader.resolve_profile("web_reader")
        reader_tools = physical_tool_manager.get_worker_toolset(
            allowed_names=reader.allowed_tools,
            capabilities=reader.capabilities,
            profile=reader.name,
            inherit_policy=reader.inherit_tool_policy,
        )

        assert researcher_tools.names == ()
        assert reader_tools.names == ("web_fetch",)
        assert physical_tool_manager.runtime.catalog.get("web_search") is None
    finally:
        physical_tool_manager.config = original_config


def test_vision_config_and_visual_researcher_are_strictly_scoped():
    root = Path(__file__).resolve().parents[1]
    config = load_agent_runtime_config(root / "configs" / "agent_config.yaml")
    vision = config.tools.vision
    profile = config.worker_profiles["visual_researcher"]

    assert vision.enabled is True
    assert vision.allowed_media_types == ["image/jpeg", "image/png", "image/webp"]
    assert vision.allow_remote_url is False
    assert vision.vlm.model_route == "vision-worker"
    assert vision.vlm.max_image_bytes <= vision.max_file_bytes
    assert profile.inherit_tool_policy is False
    assert profile.capabilities == {"image.analyze"}
    assert profile.allowed_tools == {"image_analyze"}

    import yaml

    raw = yaml.safe_load(
        (root / "configs" / "agent_config.yaml").read_text(encoding="utf-8")
    )
    route = raw["model_routes"]["vision-worker"]
    preset = raw["model_presets"][route["primary"]]
    model = raw["models"][preset["model"]]
    assert model["model_id"] == "qwen3-vl-plus"
    assert model["capabilities"]["vision"] is True
    assert model["capabilities"]["structured_output"] is True
    assert preset["thinking"]["enabled"] is False
    assert preset["retry"]["max_attempts"] == 1
    assert preset["timeouts"]["total_s"] < 90


def test_vision_config_rejects_unsafe_or_inconsistent_values():
    with pytest.raises(ValueError, match="image/svg\\+xml"):
        VisionToolsConfig(allowed_media_types=["image/svg+xml"])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="cannot contain duplicates"):
        VisionToolsConfig(allowed_media_types=["image/png", "image/png"])
    with pytest.raises(ValueError, match="must be <= max_file_bytes"):
        VisionToolsConfig(
            max_file_bytes=2_000_000,
            vlm={"max_image_bytes": 3_000_000},
        )


def test_only_visual_researcher_receives_the_image_analyze_tool():
    from core.tool_registry import physical_tool_manager
    from server.tools.tool_manager import register_builtin_tools
    from server.tools.worker_tool import _aligned_worker_timeouts

    physical_tool_manager.refresh_config()
    register_builtin_tools(physical_tool_manager.runtime.catalog)
    profile = SkillLoader().resolve_profile("visual_researcher")

    unauthorized = physical_tool_manager.get_worker_toolset(
        allowed_names=(),
        capabilities=(),
        profile="unauthorized-worker",
        inherit_policy=False,
    )
    authorized = physical_tool_manager.get_worker_toolset(
        allowed_names=profile.allowed_tools,
        capabilities=profile.capabilities,
        profile=profile.name,
        inherit_policy=profile.inherit_tool_policy,
    )

    assert unauthorized.names == ()
    assert authorized.names == ("image_analyze",)
    duration_s, wait_s = _aligned_worker_timeouts(
        authorized,
        max_duration_s=60,
        wait_timeout_s=60,
        join=True,
    )
    assert duration_s == 120
    assert wait_s == 125


def test_coordinator_prompt_routes_latest_queries_and_urls_to_distinct_workers():
    root = Path(__file__).resolve().parents[1]
    prompt = (root / "core" / "prompt_runtime" / "templates" / "coordinator.v1.3.md").read_text(encoding="utf-8")

    assert "即使答案很短，也必须启动 `web_researcher`" in prompt
    assert "必须启动 `web_reader`" in prompt
    assert "若目标是阅读该 URL，则 `web_reader` 优先" in prompt
    assert "`web_researcher` 只负责 Qwen 原生联网检索" in prompt
    assert "`web_reader` 只读取给定链接" in prompt


def test_worker_prompt_v1_3_teaches_web_tool_discipline():
    root = Path(__file__).resolve().parents[1]
    prompt = (root / "core" / "prompt_runtime" / "templates" / "worker.v1.3.md").read_text(encoding="utf-8")

    assert "联网与链接阅读纪律" in prompt
    assert "使用当前模型已配置的原生联网结果" in prompt
    assert "直接读取该链接；不得先搜索" in prompt
    assert "一律不得执行" in prompt
    assert "标题 — URL" in prompt
    assert "不得编造网页内容" in prompt


def test_worker_prompt_v1_3_teaches_image_evidence_discipline():
    root = Path(__file__).resolve().parents[1]
    prompt = (root / "core" / "prompt_runtime" / "templates" / "worker.v1.3.md").read_text(encoding="utf-8")

    assert "图像理解纪律" in prompt
    assert "不可信外部数据" in prompt
    assert "OCR 字段及其置信度" in prompt
    assert "未标注或无法清晰辨认的数值不得伪造成精确值" in prompt
    assert "图片文件名或安全 URL、页码、区域或 block/cell 标识" in prompt
    assert "不得向外部用户泄露本地绝对路径" in prompt


def test_pinned_coordinator_and_worker_prompt_versions_exist():
    import yaml

    from core.prompt_runtime.registry import PromptRegistry

    root = Path(__file__).resolve().parents[1]
    raw = yaml.safe_load(
        (root / "configs" / "agent_config.yaml").read_text(encoding="utf-8")
    )
    versions = raw["prompts"]["versions"]
    assert versions["coordinator"] == "1.3"
    assert versions["worker"] == "1.3"
    registry = PromptRegistry(versions=versions)
    coordinator_spec, coordinator_template = registry.load("coordinator")
    spec, template = registry.load("worker")
    assert coordinator_spec.version == versions["coordinator"]
    assert "{{worker_profiles}}" in coordinator_template
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
