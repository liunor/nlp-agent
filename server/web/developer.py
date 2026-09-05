"""Read-only developer control-plane snapshots for the same-origin WebUI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from configs.settings import BASE_DIR, settings
from core.identity import AuthenticatedPrincipal
from core.rbac import Permission, authorization_service


_SENSITIVE_PARTS = ("secret", "password", "api_key", "authorization", "access_token")


def _safe(value: Any, key: str = "") -> Any:
    """Remove credential values while preserving useful configuration shape."""
    lowered = key.lower()
    if any(part in lowered for part in _SENSITIVE_PARTS):
        return {"configured": bool(value)}
    if isinstance(value, dict):
        return {str(item_key): _safe(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _tool_snapshot() -> dict[str, Any]:
    from core.tool_registry import physical_tool_manager

    # The Web process can be remote from the Agent Worker and therefore may
    # never build an Agent graph. Load local descriptors for the control-plane
    # view without starting MCP clients in the Web process.
    physical_tool_manager.ensure_custom_tools()
    descriptors = []
    for descriptor in physical_tool_manager.runtime.catalog.descriptors():
        item = descriptor.model_dump(mode="json", exclude={"factory"})
        item["scopes"] = sorted(item.get("scopes", []))
        item["capabilities"] = sorted(item.get("capabilities", []))
        descriptors.append(item)
    config = physical_tool_manager.config.tools
    return {
        "catalog_revision": physical_tool_manager.catalog_revision,
        "items": descriptors,
        "policies": config.policies.model_dump(mode="json"),
        "mcp_servers": {
            name: {
                **server.model_dump(
                    mode="json", exclude={"env", "headers"}
                ),
                "credentials_configured": bool(server.env or server.headers),
            }
            for name, server in config.mcp_servers.items()
        },
        "custom": config.custom.model_dump(mode="json"),
        "custom_reload_requires_restart": bool(config.custom.modules),
    }


def _skills_snapshot() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    from core.skill_loader import skill_loader

    for skill in sorted(skill_loader.skills.values(), key=lambda item: item.name):
        stat = skill.path.stat()
        available, missing = skill.availability()
        result.append(
            {
                "name": skill.name,
                "path": skill.path.relative_to(BASE_DIR).as_posix(),
                "source": skill.source,
                "description": skill.description,
                "allowed_tools": sorted(skill.allowed_tools),
                "capabilities": sorted(skill.capabilities),
                "available": available,
                "missing_requirements": missing,
                "bytes": stat.st_size,
                "modified_at": stat.st_mtime,
            }
        )
    return result


async def developer_snapshot(
    principal: AuthenticatedPrincipal, gateway: Any
) -> dict[str, Any]:
    authorization_service.require(principal, Permission.SYSTEM_RUNTIME_INSPECT)
    health = await gateway.health()
    raw = settings._config
    providers: dict[str, Any] = {}
    for name, provider in raw.get("providers", {}).items():
        env_name = str(provider.get("api_key_env", ""))
        providers[name] = {
            "adapter": provider.get("adapter"),
            "base_url": provider.get("base_url"),
            "api_key_env": env_name,
            "api_key_configured": bool(getattr(settings, env_name, "")),
        }
    data_roots = []
    for name in ("sessions", "memory", "gateway", "observability", "tool-audit"):
        path = BASE_DIR / ".data" / name
        data_roots.append(
            {
                "name": name,
                "path": path.relative_to(BASE_DIR).as_posix(),
                "exists": path.exists(),
                "writable": path.exists() and path.is_dir(),
            }
        )
    return {
        "runtime": health.model_dump(mode="json"),
        "features": {
            "apps": {"available": False, "reason": "No app registry is configured"},
            "automations": {"available": False, "reason": "Cron runtime is not enabled"},
            "browser": {"available": False, "reason": "Browser provider is not configured"},
            "voice": {"available": False, "reason": "Voice provider is not configured"},
        },
        "models": {
            "defaults": _safe(raw.get("defaults", {})),
            "routes": _safe(raw.get("model_routes", {})),
            "models": _safe(raw.get("models", {})),
            "presets": _safe(raw.get("model_presets", {})),
            "profiles": _safe(raw.get("model_profiles", {})),
            "default_model_profile": _safe(
                raw.get("defaults", {}).get("model_profile")
                if isinstance(raw.get("defaults"), dict)
                else None
            ),
            "providers": providers,
        },
        "tools": _tool_snapshot(),
        "skills": _skills_snapshot(),
        "agents": {
            "runtime": _safe(raw.get("agent_runtime", {})),
            "profiles": _safe(raw.get("worker_profiles", {})),
            "overrides": _safe(raw.get("agents", {})),
        },
        "workspace": {"roots": data_roots},
        "web": {
            "host": raw.get("web", {}).get("host"),
            "port": raw.get("web", {}).get("port"),
            "protocol": {"http": "/api/v1", "websocket": "/ws/v1"},
        },
    }


async def developer_health(
    principal: AuthenticatedPrincipal, gateway: Any
) -> dict[str, Any]:
    """Return the lightweight live health payload used by runtime diagnostics."""
    authorization_service.require(principal, Permission.SYSTEM_RUNTIME_INSPECT)
    health = await gateway.health()
    return health.model_dump(mode="json")
