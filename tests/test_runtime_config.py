from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import time
from uuid import uuid4

import pytest
import yaml

from core import runtime_config


def test_runtime_overrides_transaction_serializes_read_modify_write(monkeypatch) -> None:
    override_path = runtime_config.BASE_DIR / f".runtime-overrides-test-{uuid4().hex}.yaml"
    lock_path = Path(f"{override_path}.lock")
    monkeypatch.setattr(runtime_config, "OVERRIDE_PATH", override_path)

    def add_value(key: str) -> None:
        with runtime_config.runtime_overrides_transaction() as overrides:
            time.sleep(0.02)
            overrides[key] = key

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(add_value, ("first", "second")))
        assert runtime_config.load_runtime_overrides() == {
            "first": "first",
            "second": "second",
        }
    finally:
        override_path.unlink(missing_ok=True)
        lock_path.unlink(missing_ok=True)


def test_legacy_overrides_cannot_replace_reserved_web_profiles(monkeypatch, tmp_path):
    base_path = tmp_path / "agent_config.yaml"
    override_path = tmp_path / "runtime-overrides.yaml"
    base = {
        "tools": {
            "policies": {
                "worker": {
                    "allowed_tools": [],
                    "allowed_capabilities": [],
                }
            },
            "web": {
                "enabled": True,
                "fetch": {"max_chars": 20_000},
            },
        },
        "worker_profiles": {
            "web_researcher": {
                "model": "worker-qwen-web",
                "execution_mode": "one_shot",
                "requires_native_search": True,
                "inherit_tool_policy": False,
            },
            "web_reader": {
                "inherit_tool_policy": False,
                "allowed_tools": ["web_fetch"],
            },
        },
    }
    legacy = {
        "tools": {
            "policies": {
                "worker": {
                    "allowed_tools": [],
                    "allowed_capabilities": ["nlp.analyze"],
                }
            },
            "web": {
                "allow_provider_override": True,
                "trusted_service_hosts": ["searxng.internal"],
                "search": {"default_provider": "tavily"},
                "fetch": {"remote_reader": {"enabled": False}},
            },
        },
        "worker_profiles": {
            "web_researcher": {
                "model": None,
                "allowed_tools": ["web_search", "web_fetch"],
            },
            "web_reader": {"allowed_tools": ["web_search"]},
            "custom_reader": {"allowed_tools": ["read_local_file"]},
        },
    }
    base_path.write_text(yaml.safe_dump(base), encoding="utf-8")
    override_path.write_text(yaml.safe_dump(legacy), encoding="utf-8")
    monkeypatch.setattr(runtime_config, "BASE_CONFIG_PATH", base_path)
    monkeypatch.setattr(runtime_config, "OVERRIDE_PATH", override_path)

    merged = runtime_config.load_runtime_config()

    assert merged["worker_profiles"]["web_researcher"] == base["worker_profiles"][
        "web_researcher"
    ]
    assert merged["worker_profiles"]["web_reader"] == base["worker_profiles"][
        "web_reader"
    ]
    assert merged["worker_profiles"]["custom_reader"] == {
        "allowed_tools": ["read_local_file"]
    }
    assert "search" not in merged["tools"]["web"]
    assert "allow_provider_override" not in merged["tools"]["web"]
    assert "trusted_service_hosts" not in merged["tools"]["web"]
    assert "remote_reader" not in merged["tools"]["web"]["fetch"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["upsert", "delete"])
async def test_developer_ui_cannot_mutate_reserved_web_profiles(operation):
    from server.web import developer_runtime

    if operation == "upsert":
        call = developer_runtime.upsert_worker_profile(
            "web_researcher", {"description": "legacy"}
        )
    else:
        call = developer_runtime.delete_worker_profile("web_reader")

    with pytest.raises(
        developer_runtime.DeveloperConfigurationError, match="reserved by the runtime"
    ):
        await call


@pytest.mark.asyncio
async def test_mcp_update_without_credentials_preserves_existing_secret_config(monkeypatch):
    from server.web import developer_runtime

    existing = {
        "tools": {
            "mcp_servers": {
                "github": {
                    "transport": "stdio",
                    "command": "python",
                    "env": {"GITHUB_TOKEN": "secret"},
                    "headers": {"X-Api-Key": "secret-header"},
                }
            }
        }
    }
    saved: dict[str, object] = {}
    monkeypatch.setattr(developer_runtime, "load_runtime_overrides", lambda: existing)

    @contextmanager
    def transaction():
        yield existing
        saved.update(existing)

    monkeypatch.setattr(developer_runtime, "runtime_overrides_transaction", transaction)
    monkeypatch.setattr(developer_runtime, "test_mcp_server", lambda _name, _config: _connected())
    monkeypatch.setattr(developer_runtime, "reload_runtime", lambda **_kwargs: _reloaded())

    result = await developer_runtime.upsert_mcp_server(
        "github", {"transport": "stdio", "command": "uv", "args": ["run", "server.py"]}
    )

    config = saved["tools"]["mcp_servers"]["github"]  # type: ignore[index]
    assert config["env"] == {"GITHUB_TOKEN": "secret"}  # type: ignore[index]
    assert config["headers"] == {"X-Api-Key": "secret-header"}  # type: ignore[index]
    assert config["command"] == "uv"  # type: ignore[index]
    assert result["server"] == "github"


async def _connected() -> dict[str, object]:
    return {"ok": True, "server": "github", "tools": []}


async def _reloaded() -> dict[str, object]:
    return {"restart_required": False}
