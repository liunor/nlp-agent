"""Safe model/provider metadata shared by the monitor and developer surfaces."""

from __future__ import annotations

from typing import Any

from configs.settings import settings


def monitor_model_catalog() -> dict[str, dict[str, dict[str, Any]]]:
    """Expose the same configured model registry without credential values."""
    raw = settings._config
    providers: dict[str, dict[str, Any]] = {}
    for name, provider in (raw.get("providers", {}) or {}).items():
        if not isinstance(provider, dict):
            continue
        env_name = str(provider.get("api_key_env", ""))
        providers[str(name)] = {
            "adapter": provider.get("adapter"),
            "base_url": provider.get("base_url"),
            "api_key_configured": bool(getattr(settings, env_name, "")),
        }

    models: dict[str, dict[str, Any]] = {}
    presets_by_model: dict[str, list[str]] = {}
    for preset_name, preset in (raw.get("model_presets", {}) or {}).items():
        if not isinstance(preset, dict):
            continue
        model_name = str(preset.get("model") or "")
        if model_name:
            presets_by_model.setdefault(model_name, []).append(str(preset_name))
    for name, model in (raw.get("models", {}) or {}).items():
        if not isinstance(model, dict):
            continue
        item = {
            "provider": model.get("provider"),
            "model_id": model.get("model_id"),
            "context_window_tokens": model.get("context_window_tokens"),
            "capabilities": model.get("capabilities", {}),
            "profile_names": sorted(presets_by_model.get(str(name), [])),
        }
        models[str(name)] = item
        model_id = str(model.get("model_id") or "")
        if model_id and model_id not in models:
            models[model_id] = {**item, "catalog_name": str(name)}

    return {"providers": providers, "models": models}
