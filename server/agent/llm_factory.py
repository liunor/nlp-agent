"""Compatibility facade over the typed provider/model runtime."""

from core.model_runtime.factory import get_global_model_factory
from core.model_runtime.runtime import ResilientChatModel
from core.model_runtime.selection import current_model_profile, current_thinking_enabled, current_model_snapshot
from configs.settings import settings


def _selected_profile(requested: str | None = None) -> str | None:
    return requested or current_model_profile()


def _thinking_kwargs() -> dict[str, bool]:
    enabled = current_thinking_enabled()
    return {"thinking_enabled": enabled} if enabled is not None else {}


def get_planner_llm(model_profile: str | None = None) -> ResilientChatModel:
    factory = get_global_model_factory()
    if snapshot := current_model_snapshot():
        return factory.build_snapshot_role(snapshot, "coordinator")
    if selected := _selected_profile(model_profile):
        return factory.build_profile_role(selected, "coordinator", **_thinking_kwargs())
    return factory.build_route("coordinator", **_thinking_kwargs())


def resolve_worker_model_name(
    agent_name: str | None = None,
    requested_model: str | None = None,
    model_profile: str | None = None,
) -> str:
    if snapshot := current_model_snapshot():
        if agent_name == "web_researcher":
            return "worker-qwen-web"
        if agent_name in {"web_reader", "visual_researcher"}:
            return snapshot.system_worker[0].preset_name
        if snapshot.worker_override:
            return snapshot.worker[0].preset_name
        allowed = {item.preset_name for item in snapshot.worker}
        if requested_model not in (None, "", "inherit") and requested_model not in allowed:
            raise ValueError("Worker model override is outside this Turn execution snapshot")
        return str(requested_model) if requested_model not in (None, "", "inherit") else snapshot.worker[0].preset_name
    if settings.NLP_AGENT_WORKER_MODEL:
        return settings.NLP_AGENT_WORKER_MODEL
    if requested_model not in (None, "", "inherit"):
        return str(requested_model)
    factory = get_global_model_factory()
    if selected := _selected_profile(model_profile):
        return factory.profile_preset(selected, "worker")
    return settings._resolve_worker_model(agent_name)


def worker_model_uses_native_search(model_name: str) -> bool:
    """Return whether a configured Worker preset enables provider-native search."""
    snapshot = current_model_snapshot()
    if snapshot:
        from core.model_runtime.contracts import ModelRuntimeConfig
        preset = ModelRuntimeConfig.model_validate_json(snapshot.runtime_json).model_presets.get(model_name)
    else:
        preset = get_global_model_factory().config.model_presets.get(model_name)
    return bool(preset and preset.native_search.enabled)


def get_tool_llm(model_profile: str | None = None) -> ResilientChatModel:
    """Return the selected profile's Worker model or the default Worker route."""
    return get_worker_llm(model_profile=model_profile)


def get_utility_llm(model_profile: str | None = None) -> ResilientChatModel:
    """Return the selected profile's utility model for compression and curation."""
    factory = get_global_model_factory()
    if snapshot := current_model_snapshot():
        return factory.build_snapshot_role(snapshot, "utility")
    if selected := _selected_profile(model_profile):
        return factory.build_profile_role(selected, "utility", **_thinking_kwargs())
    return factory.build_route("utility", **_thinking_kwargs())


def get_worker_llm(
    agent_name: str | None = None,
    tool_specified_model: str | None = None,
    model_profile: str | None = None,
) -> ResilientChatModel:
    factory = get_global_model_factory()
    if snapshot := current_model_snapshot():
        if agent_name == "web_researcher":
            return factory.build_snapshot_system_preset(snapshot, "worker-qwen-web")
        if agent_name in {"web_reader", "visual_researcher"}:
            return factory.build_snapshot_role(snapshot, "system_worker")
        requested = resolve_worker_model_name(agent_name, tool_specified_model, model_profile)
        if (not snapshot.worker_override and tool_specified_model not in (None, "", "inherit")
                and requested != snapshot.worker[0].preset_name):
            return factory.build_snapshot_role(snapshot, "worker", preset_name=requested)
        return factory.build_snapshot_role(snapshot, "worker")
    selected = _selected_profile(model_profile)
    has_explicit_override = bool(settings.NLP_AGENT_WORKER_MODEL) or (
        tool_specified_model not in (None, "", "inherit")
    )
    if selected and not has_explicit_override:
        return factory.build_profile_role(selected, "worker", **_thinking_kwargs())
    requested = resolve_worker_model_name(
        agent_name, tool_specified_model, model_profile
    )
    default = settings._config.get("model_routes", {}).get("worker", {}).get("primary")
    if requested == default:
        return factory.build_route("worker", **_thinking_kwargs())
    return factory.build_override(requested, base_route="worker", **_thinking_kwargs())
