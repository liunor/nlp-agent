"""The developer editor cannot override user-owned reasoning policy."""

from copy import deepcopy
from contextlib import contextmanager

import pytest
import yaml

from core import runtime_config
from server.web import developer_runtime


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("thinking", {"enabled": False, "effort": "none"}),
        ("thinking_level", "quick"),
        ("reasoning_effort", "low"),
        ("extra_body", {"thinking": {"type": "disabled"}}),
    ],
)
async def test_old_developer_fields_are_rejected_before_writing(
    monkeypatch, field, value
):
    writes = []

    @contextmanager
    def transaction():
        writes.append(True)
        yield {}

    monkeypatch.setattr(developer_runtime, "runtime_overrides_transaction", transaction)
    presets = {
        name: {key: item for key, item in preset.items() if key != "thinking"}
        for name, preset in runtime_config.load_runtime_config()[
            "model_presets"
        ].items()
    }
    presets["coordinator-fast"][field] = value
    with pytest.raises(developer_runtime.DeveloperConfigurationError):
        await developer_runtime.update_model_presets(presets)
    assert writes == []


def test_legacy_reasoning_overrides_cannot_change_chat_or_system_policy(
    monkeypatch, tmp_path
):
    base = {
        "model_presets": {
            "chat": {
                "model": "m",
                "thinking": {"enabled": True, "effort": "high"},
                "generation": {"max_output_tokens": 99},
            },
            "utility": {"model": "m", "thinking": {"enabled": False, "effort": "none"}},
        }
    }
    legacy = {
        "model_presets": {
            "chat": {
                "thinking": {"enabled": False, "effort": "none"},
                "generation": {"max_output_tokens": 50},
            },
            "utility": {"thinking": {"enabled": True, "effort": "high"}},
        },
        "defaults": {"worker_model": "worker-fast"},
        "unrelated": {"keep": True},
    }
    base_path, override_path = tmp_path / "base.yaml", tmp_path / "overrides.yaml"
    base_path.write_text(yaml.safe_dump(base), encoding="utf-8")
    override_path.write_text(yaml.safe_dump(legacy), encoding="utf-8")
    monkeypatch.setattr(runtime_config, "BASE_CONFIG_PATH", base_path)
    monkeypatch.setattr(runtime_config, "OVERRIDE_PATH", override_path)
    actual = runtime_config.load_runtime_config()
    for name in ("chat", "utility"):
        assert (
            actual["model_presets"][name]["thinking"]
            == base["model_presets"][name]["thinking"]
        )
    assert actual["model_presets"]["chat"]["generation"]["max_output_tokens"] == 50
    assert actual["unrelated"] == {"keep": True}
    runtime_config.save_runtime_overrides(runtime_config.load_runtime_overrides())
    saved = yaml.safe_load(override_path.read_text(encoding="utf-8"))
    assert all("thinking" not in item for item in saved["model_presets"].values())
    assert saved["defaults"] == legacy["defaults"]


@pytest.mark.asyncio
async def test_editing_generation_retains_server_thinking_and_omitted_presets(
    monkeypatch,
):
    raw = runtime_config.load_runtime_config()
    before = deepcopy(raw)
    saved = []
    monkeypatch.setattr(developer_runtime, "load_runtime_config", lambda: raw)
    @contextmanager
    def transaction():
        value = {}
        yield value
        saved.append(deepcopy(value))

    monkeypatch.setattr(developer_runtime, "runtime_overrides_transaction", transaction)

    async def reload(**_):
        return {}

    monkeypatch.setattr(developer_runtime, "reload_runtime", reload)
    result = await developer_runtime.update_model_presets(
        {"coordinator-fast": {"generation": {"max_output_tokens": 2048}}}
    )
    assert (
        saved[0]["model_presets"]["coordinator-fast"]["generation"]["max_output_tokens"]
        == 2048
    )
    assert all("thinking" not in item for item in result["presets"].values())
    assert raw == before


@pytest.mark.asyncio
async def test_developer_snapshot_has_no_reasoning_editor_fields(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from server.web import developer
    from core.identity import AuthenticatedPrincipal
    from gateway.contracts import GatewayHealth

    monkeypatch.setattr(developer, "_tool_snapshot", lambda: {})
    monkeypatch.setattr(developer, "_skills_snapshot", lambda: [])
    snapshot = await developer.developer_snapshot(
        AuthenticatedPrincipal.system_admin(),
        SimpleNamespace(
            health=AsyncMock(
                return_value=GatewayHealth(
                    status="ready",
                    started=True,
                    accepting_turns=True,
                    active_turns=0,
                    subscribers=0,
                    database="test",
                    durable_events=0,
                )
            )
        ),
    )
    assert snapshot["models"]["presets"]
    assert all(
        "thinking" not in item for item in snapshot["models"]["presets"].values()
    )


def test_http_rejects_old_reasoning_fields_without_touching_overrides(monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from server.web.app import create_app
    from server.web.auth import SameOriginSessionAuth
    from core.identity import AuthenticatedPrincipal

    writes = []

    @contextmanager
    def transaction():
        writes.append(True)
        yield {}

    monkeypatch.setattr(developer_runtime, "runtime_overrides_transaction", transaction)
    auth = SameOriginSessionAuth(
        secret="step5-test-secret-that-is-long-enough",
        allowed_origins=["http://testserver"],
    )
    app = create_app(auth=auth, allowed_hosts=["testserver"])
    app.state.gateway = SimpleNamespace()
    token, claims = auth.issue(AuthenticatedPrincipal.system_admin())
    client = TestClient(app, raise_server_exceptions=True)
    try:
        client.cookies.set(auth.cookie_name, token)
        response = client.put(
            "/api/v1/developer/models/presets",
            json={
                "presets": {
                    "coordinator-fast": {
                        "thinking": {"enabled": False, "effort": "none"}
                    }
                }
            },
            headers={"Origin": "http://testserver", "X-CSRF-Token": claims.csrf_token},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "developer_configuration_invalid"
    finally:
        client.close()
    assert writes == []
