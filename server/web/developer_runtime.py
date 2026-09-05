"""Writable Developer control plane for Tool policies, Skills, and MCP servers."""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from core.runtime_config import (
    BASE_DIR,
    RESERVED_WORKER_PROFILES,
    load_runtime_config,
    load_runtime_overrides,
    runtime_overrides_transaction,
)
from core.model_runtime.contracts import (
    ModelPresetConfig,
    ModelProfileConfig,
    ModelRouteConfig,
    ModelRuntimeConfig,
    ProviderConfig,
)
from core.tool_config import CustomToolsConfig, MCPServerConfig, ToolPoliciesConfig, WorkerProfileSpec
from core.tool_runtime import ToolCatalog


_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


class DeveloperConfigurationError(ValueError):
    pass


def _require_name(value: str, label: str) -> str:
    if not _NAME.fullmatch(value):
        raise DeveloperConfigurationError(f"{label} must contain only letters, digits, _ or -")
    return value


def _section(overrides: dict[str, Any], name: str) -> dict[str, Any]:
    value = overrides.setdefault(name, {})
    if not isinstance(value, dict):
        raise DeveloperConfigurationError(f"invalid persisted {name} override")
    return value


def _merge_stored_mcp_credentials(
    name: str,
    config: dict[str, Any],
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Restore credentials omitted from browser payloads during MCP operations."""
    persisted = load_runtime_overrides() if overrides is None else overrides
    stored_tools = persisted.get("tools")
    stored_servers = stored_tools.get("mcp_servers") if isinstance(stored_tools, dict) else None
    stored = stored_servers.get(name) if isinstance(stored_servers, dict) else None
    candidate = dict(config)
    if isinstance(stored, dict):
        # Secret values are intentionally omitted from the browser snapshot.
        # Preserve them during ordinary edits unless the caller explicitly
        # sends an env/headers field (including an explicit empty mapping).
        for key in ("env", "headers"):
            if key not in candidate and isinstance(stored.get(key), dict):
                candidate[key] = stored[key]
    return candidate


async def reload_runtime(*, reload_mcp: bool = False, reload_skills: bool = False) -> dict[str, Any]:
    """Refresh config consumers. Existing Worker grants intentionally stay immutable."""
    from configs.settings import settings
    from core.model_runtime import factory as model_factory
    from core.skill_loader import skill_loader
    from core.tool_registry import physical_tool_manager
    from server.agent.node.coordinator import invalidate_coordinator_caches

    settings._config = __import__("core.runtime_config", fromlist=["load_runtime_config"]).load_runtime_config()
    # ModelFactory caches provider clients and typed runtime config. Rebuild it
    # after any developer override so the next turn observes the new route.
    model_factory._global_model_factory = None
    physical_tool_manager.refresh_config()
    if reload_skills:
        skill_loader.profiles = physical_tool_manager.config.worker_profiles
        skill_loader.reload()
    if reload_mcp:
        await physical_tool_manager.runtime.start_mcp(physical_tool_manager.config.tools.mcp_servers)
    invalidate_coordinator_caches()
    return {
        "catalog_revision": physical_tool_manager.catalog_revision,
        "mcp_reloaded": reload_mcp,
        "skills_reloaded": reload_skills,
        "restart_required": False,
    }


def _model_runtime_payload(raw: dict[str, Any]) -> dict[str, Any]:
    defaults = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}
    return {
        "providers": raw.get("providers", {}),
        "models": raw.get("models", {}),
        "model_presets": raw.get("model_presets", {}),
        "model_routes": raw.get("model_routes", {}),
        "model_profiles": raw.get("model_profiles", {}),
        "default_model_profile": defaults.get("model_profile"),
    }


def _validated_model_update(section: str, name: str, value: dict[str, Any]) -> dict[str, Any]:
    name = _require_name(name, f"Model {section} name")
    current = deepcopy(load_runtime_config())
    values = current.setdefault(section, {})
    if not isinstance(values, dict):
        raise DeveloperConfigurationError(f"invalid persisted {section} configuration")
    values[name] = value
    validated = ModelRuntimeConfig.model_validate(_model_runtime_payload(current))
    return validated.model_dump(mode="json")


def _persist_model_entry(section: str, name: str, value: dict[str, Any]) -> None:
    with runtime_overrides_transaction() as overrides:
        values = _section(overrides, section)
        values[name] = value


async def upsert_model_provider(name: str, config: dict[str, Any]) -> dict[str, Any]:
    name = _require_name(name, "Model provider name")
    candidate = dict(config)
    current = load_runtime_config()
    existing = current.get("providers", {}).get(name) if isinstance(current.get("providers"), dict) else None
    if isinstance(existing, dict) and "default_headers" not in candidate:
        candidate["default_headers"] = existing.get("default_headers", {})
    provider = ProviderConfig.model_validate(candidate)
    validated = _validated_model_update("providers", name, provider.model_dump(mode="json"))
    _persist_model_entry("providers", name, validated["providers"][name])
    result = await reload_runtime()
    return {**result, "provider": name}


async def upsert_model_preset(name: str, config: dict[str, Any]) -> dict[str, Any]:
    preset = ModelPresetConfig.model_validate(config)
    validated = _validated_model_update("model_presets", name, preset.model_dump(mode="json"))
    _persist_model_entry("model_presets", name, validated["model_presets"][name])
    result = await reload_runtime()
    return {**result, "preset": _require_name(name, "Model preset name")}


async def upsert_model_route(name: str, config: dict[str, Any]) -> dict[str, Any]:
    route = ModelRouteConfig.model_validate(config)
    validated = _validated_model_update("model_routes", name, route.model_dump(mode="json"))
    _persist_model_entry("model_routes", name, validated["model_routes"][name])
    result = await reload_runtime()
    return {**result, "route": _require_name(name, "Model route name")}


async def upsert_model_profile(name: str, config: dict[str, Any]) -> dict[str, Any]:
    profile = ModelProfileConfig.model_validate(config)
    validated = _validated_model_update("model_profiles", name, profile.model_dump(mode="json"))
    _persist_model_entry("model_profiles", name, validated["model_profiles"][name])
    result = await reload_runtime()
    return {**result, "profile": _require_name(name, "Model profile name")}


async def update_tool_policies(policies: dict[str, Any]) -> dict[str, Any]:
    validated = ToolPoliciesConfig.model_validate(policies)
    with runtime_overrides_transaction() as overrides:
        _section(overrides, "tools")["policies"] = validated.model_dump(mode="json")
    return await reload_runtime()


async def update_custom_tools(custom: dict[str, Any]) -> dict[str, Any]:
    """Persist extension discovery config; changing Python imports requires a safe restart."""
    validated = CustomToolsConfig.model_validate(custom)
    with runtime_overrides_transaction() as overrides:
        _section(overrides, "tools")["custom"] = validated.model_dump(mode="json")
    await reload_runtime()
    return {"restart_required": True, "reason": "custom Python tool modules reload on next runtime start"}


async def upsert_mcp_server(name: str, config: dict[str, Any]) -> dict[str, Any]:
    name = _require_name(name, "MCP server name")
    overrides = load_runtime_overrides()
    candidate = _merge_stored_mcp_credentials(name, config, overrides=overrides)
    validated = MCPServerConfig.model_validate(candidate)
    await test_mcp_server(name, validated.model_dump(mode="json"))
    with runtime_overrides_transaction() as overrides:
        tools = _section(overrides, "tools")
        servers = tools.setdefault("mcp_servers", {})
        if not isinstance(servers, dict):
            raise DeveloperConfigurationError("invalid persisted MCP server override")
        servers[name] = validated.model_dump(mode="json")
    result = await reload_runtime(reload_mcp=True)
    return {**result, "server": name}


async def delete_mcp_server(name: str) -> dict[str, Any]:
    name = _require_name(name, "MCP server name")
    with runtime_overrides_transaction() as overrides:
        tools = _section(overrides, "tools")
        servers = tools.setdefault("mcp_servers", {})
        if not isinstance(servers, dict):
            raise DeveloperConfigurationError("invalid persisted MCP server override")
        servers.pop(name, None)
    result = await reload_runtime(reload_mcp=True)
    return {**result, "server": name}


async def test_mcp_server(name: str, config: dict[str, Any]) -> dict[str, Any]:
    """Connect/discover through an isolated catalog; no trial tools leak into the live runtime."""
    name = _require_name(name, "MCP server name")
    validated = MCPServerConfig.model_validate(_merge_stored_mcp_credentials(name, config))
    from core.mcp_runtime import MCPRuntime

    catalog = ToolCatalog()
    runtime = MCPRuntime(catalog)
    try:
        await runtime.connect_all({name: validated})
        return {"ok": True, "server": name, "tools": list(catalog.names())}
    finally:
        await runtime.close()


def _skill_path(name: str) -> Path:
    return BASE_DIR / ".data" / "skills" / _require_name(name, "Skill name") / "SKILL.md"


async def upsert_skill(name: str, content: str) -> dict[str, Any]:
    path = _skill_path(name)
    if len(content.encode("utf-8")) > 200_000:
        raise DeveloperConfigurationError("Skill content exceeds 200KB")
    if not content.lstrip().startswith("---"):
        raise DeveloperConfigurationError("Skill must begin with YAML frontmatter")
    match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", content, re.DOTALL)
    if match is None:
        raise DeveloperConfigurationError("Skill YAML frontmatter is incomplete")
    metadata = yaml.safe_load(match.group(1)) or {}
    if not isinstance(metadata, dict) or metadata.get("name") != name:
        raise DeveloperConfigurationError("Skill frontmatter name must match the Skill name")
    if not isinstance(metadata.get("description") or metadata.get("when_to_use"), str):
        raise DeveloperConfigurationError("Skill frontmatter requires description or when_to_use")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.replace("\r\n", "\n"), encoding="utf-8")
    await reload_runtime(reload_skills=True)
    return {"name": name, "path": path.relative_to(BASE_DIR).as_posix()}


def read_skill(name: str) -> dict[str, Any]:
    name = _require_name(name, "Skill name")
    from core.skill_loader import skill_loader

    skill = skill_loader.skills.get(name)
    if skill is None:
        raise FileNotFoundError(name)
    path = skill.path
    return {"name": name, "content": path.read_text(encoding="utf-8")}


async def delete_skill(name: str) -> dict[str, Any]:
    path = _skill_path(name)
    if path.exists():
        path.unlink()
        try:
            path.parent.rmdir()
        except OSError:
            pass
    await reload_runtime(reload_skills=True)
    return {"name": name}


async def upsert_worker_profile(name: str, profile: dict[str, Any]) -> dict[str, Any]:
    name = _require_name(name, "Worker profile name")
    if name in RESERVED_WORKER_PROFILES:
        raise DeveloperConfigurationError(
            f"Worker profile {name!r} is reserved by the runtime"
        )
    validated = WorkerProfileSpec.model_validate({"name": name, **profile})
    with runtime_overrides_transaction() as overrides:
        _section(overrides, "worker_profiles")[name] = validated.model_dump(
            mode="json", exclude={"name"}
        )
    return {**await reload_runtime(reload_skills=True), "profile": name}


async def delete_worker_profile(name: str) -> dict[str, Any]:
    name = _require_name(name, "Worker profile name")
    if name in RESERVED_WORKER_PROFILES:
        raise DeveloperConfigurationError(
            f"Worker profile {name!r} is reserved by the runtime"
        )
    with runtime_overrides_transaction() as overrides:
        profiles = _section(overrides, "worker_profiles")
        profiles.pop(name, None)
    return {**await reload_runtime(reload_skills=True), "profile": name}
