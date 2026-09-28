from contextlib import contextmanager

import pytest


MODEL_CONFIG = {
    "providers": {
        "deepseek": {
            "adapter": "deepseek",
            "base_url": "https://api.deepseek.com",
            "api_key_env": "DEEPSEEK_API_KEY",
            "default_headers": {},
        }
    },
    "models": {
        "deepseek-v4-flash": {
            "provider": "deepseek",
            "model_id": "deepseek-v4-flash",
            "context_window_tokens": 1000000,
            "max_output_tokens": 384000,
            "capabilities": {"streaming": True, "tool_calls": True, "thinking": True},
        }
    },
    "model_presets": {
        "worker-fast": {
            "model": "deepseek-v4-flash",
            "thinking": {"enabled": True, "effort": "high"},
            "generation": {"max_output_tokens": 24000},
        },
        "worker-safe": {
            "model": "deepseek-v4-flash",
            "thinking": {"enabled": False, "effort": "none"},
            "generation": {"max_output_tokens": 12000},
        },
    },
    "model_routes": {"worker": {"primary": "worker-fast", "fallbacks": []}},
    "model_profiles": {},
    "defaults": {},
}


def capture_transaction(saved: dict[str, object]):
    @contextmanager
    def transaction():
        overrides: dict[str, object] = {}
        yield overrides
        saved.update(overrides)

    return transaction


@pytest.mark.asyncio
async def test_model_provider_update_persists_and_reloads(monkeypatch):
    from server.web import developer_runtime

    saved: dict[str, object] = {}
    monkeypatch.setattr(developer_runtime, "load_runtime_config", lambda: MODEL_CONFIG)
    monkeypatch.setattr(developer_runtime, "load_runtime_overrides", lambda: {})
    monkeypatch.setattr(developer_runtime, "runtime_overrides_transaction", capture_transaction(saved))
    monkeypatch.setattr(developer_runtime, "reload_runtime", lambda **_kwargs: _reloaded())

    result = await developer_runtime.upsert_model_provider(
        "deepseek",
        {
            "adapter": "deepseek",
            "base_url": "https://proxy.example/v1",
            "api_key_env": "DEEPSEEK_API_KEY",
            "default_headers": {},
        },
    )

    assert saved["providers"]["deepseek"]["base_url"] == "https://proxy.example/v1"  # type: ignore[index]
    assert result["provider"] == "deepseek"


@pytest.mark.asyncio
async def test_model_preset_update_uses_full_runtime_validation(monkeypatch):
    from server.web import developer_runtime

    saved: dict[str, object] = {}
    monkeypatch.setattr(developer_runtime, "load_runtime_config", lambda: MODEL_CONFIG)
    monkeypatch.setattr(developer_runtime, "load_runtime_overrides", lambda: {})
    monkeypatch.setattr(developer_runtime, "runtime_overrides_transaction", capture_transaction(saved))
    monkeypatch.setattr(developer_runtime, "reload_runtime", lambda **_kwargs: _reloaded())

    result = await developer_runtime.upsert_model_preset(
        "worker-fast",
        {
            "model": "deepseek-v4-flash",
            "thinking": {"enabled": True, "effort": "high"},
            "generation": {"max_output_tokens": 16000},
        },
    )

    assert saved["model_presets"]["worker-fast"]["generation"]["max_output_tokens"] == 16000  # type: ignore[index]
    assert result["preset"] == "worker-fast"


@pytest.mark.asyncio
async def test_model_route_update_rejects_unknown_preset_before_persisting(monkeypatch):
    from server.web import developer_runtime

    saved: dict[str, object] = {}
    monkeypatch.setattr(developer_runtime, "load_runtime_config", lambda: MODEL_CONFIG)
    monkeypatch.setattr(developer_runtime, "load_runtime_overrides", lambda: {})

    with pytest.raises(ValueError, match="unknown presets"):
        await developer_runtime.upsert_model_route(
            "worker", {"primary": "missing", "fallbacks": []}
        )

    assert saved == {}


async def _reloaded() -> dict[str, object]:
    return {"restart_required": False}
