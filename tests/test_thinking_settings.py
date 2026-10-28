"""User-owned persisted thinking preferences and public capability projection."""

import pytest
from pydantic import ValidationError

from core.identity import AuthenticatedPrincipal
from gateway.core import BackendGateway
from gateway.repository import GatewayRepository
from server.web.contracts import UpdateSettingsBody


@pytest.mark.parametrize(("legacy", "expected"), [({}, "light"), ({"show_reasoning": False}, "quick"), ({"show_reasoning": True}, "light")])
def test_legacy_user_migration_is_persisted_once_and_does_not_follow_later_display_changes(tmp_path, legacy, expected):
    repo = GatewayRepository(tmp_path / "settings.sqlite3")
    # Seed historical data directly: current writes must already use migration.
    import json
    with repo._conn:
        repo._conn.execute(
            "INSERT INTO gateway_user_settings(user_id,revision,settings_json,updated_at) VALUES(?,?,?,?)",
            ("a", 3, json.dumps(legacy), "2026-10-22"),
        )
    first = repo.get_user_settings("a")
    assert first["settings"]["thinking_level"] == expected
    assert repo.get_user_settings("a") == first
    updated = repo.update_user_settings("a", {"show_reasoning": not legacy.get("show_reasoning", False)})
    assert updated["settings"]["thinking_level"] == expected
    assert updated["revision"] == first["revision"] + 1


def test_explicit_level_wins_and_other_users_do_not_change(tmp_path):
    repo = GatewayRepository(tmp_path / "settings.sqlite3")
    repo.update_user_settings("a", {"thinking_level": "extreme", "theme": "dark"})
    b = repo.get_user_settings("b")
    repo.update_user_settings("a", {"show_reasoning": False})
    assert repo.get_user_settings("a")["settings"] == {"thinking_level": "extreme", "theme": "dark", "show_reasoning": False}
    assert b["settings"]["thinking_level"] == "light"
    assert repo.get_user_settings("b") == b


def test_sqlite_lazy_migration_preserves_a_concurrent_write_from_another_connection(tmp_path, monkeypatch):
    import json
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import gateway.repository as repository_module

    path = tmp_path / "concurrent.sqlite3"
    reader = GatewayRepository(path)
    writer = GatewayRepository(path)
    with reader._conn:
        reader._conn.execute(
            "INSERT INTO gateway_user_settings(user_id,revision,settings_json,updated_at) VALUES(?,?,?,?)",
            ("a", 2, json.dumps({"show_reasoning": False, "theme": "light"}), "old"),
        )
    read_started = threading.Event()
    continue_read = threading.Event()
    write_done = threading.Event()
    migrate = repository_module.migrate_thinking_settings

    def pause_reader(settings):
        if threading.current_thread().name.endswith("_0") and not read_started.is_set():
            read_started.set()
            assert continue_read.wait(5)
        return migrate(settings)

    def save_new_selection():
        try:
            return writer.update_user_settings("a", {"thinking_level": "deep", "theme": "dark"})
        finally:
            write_done.set()

    monkeypatch.setattr(repository_module, "migrate_thinking_settings", pause_reader)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="thinking-migration") as pool:
        reading = pool.submit(reader.get_user_settings, "a")
        assert read_started.wait(5)
        writing = pool.submit(save_new_selection)
        # Old code lets the competing writer finish before its stale migration.
        # Correct code holds the DB write lock, so the writer completes afterward.
        write_done.wait(0.5)
        continue_read.set()
        reading.result(timeout=10)
        writing.result(timeout=10)
    final = reader.get_user_settings("a")["settings"]
    assert final["thinking_level"] == "deep"
    assert final["theme"] == "dark"


@pytest.mark.asyncio
async def test_gateway_reads_and_writes_only_the_authenticated_user(tmp_path):
    gateway = object.__new__(BackendGateway)
    gateway.repository = GatewayRepository(tmp_path / "settings.sqlite3")
    a = AuthenticatedPrincipal(user_id="a")
    b = AuthenticatedPrincipal(user_id="b")
    await gateway.update_user_settings(a, {"thinking_level": "deep"})
    assert (await gateway.get_user_settings(b))["settings"]["thinking_level"] == "light"
    assert (await gateway.get_user_settings(a))["settings"]["thinking_level"] == "deep"


def test_settings_contract_accepts_only_product_levels_and_forbids_forged_owner():
    assert UpdateSettingsBody(thinking_level="deep").model_dump(exclude_none=True) == {"thinking_level": "deep"}
    for body in ({"thinking_level": "high"}, {"thinking_level": "deep", "user_id": "victim"}):
        with pytest.raises(ValidationError):
            UpdateSettingsBody.model_validate(body)


def test_profile_support_intersects_ordinary_roles_and_ignores_system_utility():
    from core.model_runtime.factory import ModelFactory
    from core.runtime_config import load_runtime_config
    from core.model_runtime.thinking import supported_profile_thinking_levels

    config = ModelFactory.config_from_raw(load_runtime_config())
    assert [level.value for level in supported_profile_thinking_levels(config, "deepseek")] == ["quick", "light", "deep", "extreme"]
    changed_model = config.models["deepseek-v4-flash"].model_copy(update={"model_id": "unverified"})
    changed = config.model_copy(update={"models": {**config.models, "deepseek-v4-flash": changed_model}})
    assert supported_profile_thinking_levels(changed, "deepseek") == ()
    assert supported_profile_thinking_levels(config, "qwen") == ()


def test_public_model_profiles_return_supported_levels_without_billing(monkeypatch):
    from core.model_runtime.factory import ModelFactory
    from core.runtime_config import load_runtime_config

    factory = ModelFactory(ModelFactory.config_from_raw(load_runtime_config()))
    monkeypatch.setattr(factory, "_api_key", lambda _name: "test-credential")
    profiles = factory.public_profiles()
    assert profiles["deepseek"]["supported_levels"] == ["quick", "light", "deep", "extreme"]
    assert profiles["qwen"]["supported_levels"] == []
    assert set(profiles["deepseek"]) == {"label", "provider", "available", "supported_levels"}
    monkeypatch.setattr(factory, "_api_key", lambda _name: "")
    assert factory.public_profiles()["deepseek"]["supported_levels"] == []


def test_mysql_migration_locks_and_updates_only_the_requested_user():
    from unittest.mock import MagicMock
    from gateway.mysql_repository import MySQLGatewayRepository

    repo = object.__new__(MySQLGatewayRepository)
    connection = MagicMock()
    repo._engine = MagicMock()
    repo._engine.begin.return_value.__enter__.return_value = connection
    connection.execute.return_value.mappings.return_value.one.side_effect = [
        {"revision": 4, "preferences_json": {"show_reasoning": False}, "updated_at": "old"},
        {"revision": 5, "preferences_json": {"show_reasoning": False, "thinking_level": "quick"}, "updated_at": "new"},
        {"revision": 5, "preferences_json": {"show_reasoning": False, "thinking_level": "quick"}, "updated_at": "new"},
    ]
    first = repo.get_user_settings("a")
    assert first == repo.get_user_settings("a")
    assert first["settings"]["thinking_level"] == "quick"
    queries = [(str(call.args[0]), call.args[1]) for call in connection.execute.call_args_list]
    assert all(parameters["id"] == "a" for _, parameters in queries)
    assert sum(query.startswith("UPDATE ") for query, _ in queries) == 1
    assert all("FOR UPDATE" in query for query, _ in queries if query.startswith("SELECT "))


def test_settings_http_uses_authenticated_cookie_not_a_submitted_owner(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from core.model_runtime.factory import get_global_model_factory
    from server.web.app import create_app
    from server.web.auth import SameOriginSessionAuth

    gateway = object.__new__(BackendGateway)
    gateway.repository = GatewayRepository(tmp_path / "http-settings.sqlite3")
    gateway._database_runtime = None
    auth = SameOriginSessionAuth(secret="settings-test-secret-that-is-long-enough", allowed_origins=["http://testserver"])
    app = create_app(auth=auth, allowed_hosts=["testserver"])
    app.state.gateway = gateway
    monkeypatch.setattr(get_global_model_factory(), "_api_key", lambda _name: "test-credential")
    client = TestClient(app)
    assert client.get("/api/v1/settings").status_code == 401
    a_token, a_claims = auth.issue(AuthenticatedPrincipal(user_id="a", roles=frozenset({"student"})))
    b_token, b_claims = auth.issue(AuthenticatedPrincipal(user_id="b", roles=frozenset({"student"})))
    client.cookies.clear()
    client.cookies.set(auth.cookie_name, a_token)
    headers = {"Origin": "http://testserver", "X-CSRF-Token": a_claims.csrf_token}
    initial = client.get("/api/v1/settings").json()
    assert initial["preferences"]["settings"]["thinking_level"] == "light"
    assert initial["runtime"]["model_profiles"]["deepseek"]["supported_levels"] == ["quick", "light", "deep", "extreme"]
    assert client.patch("/api/v1/settings", json={"thinking_level": "deep", "user_id": "b"}, headers=headers).status_code == 422
    updated = client.patch("/api/v1/settings?user_id=b", json={"thinking_level": "deep"}, headers=headers)
    assert updated.status_code == 200
    assert updated.json()["settings"]["thinking_level"] == "deep"
    assert client.patch("/api/v1/settings", json={"thinking_level": "high"}, headers=headers).status_code == 422
    client.cookies.clear()
    client.cookies.set(auth.cookie_name, b_token)
    assert client.get("/api/v1/settings").json()["preferences"]["settings"]["thinking_level"] == "light"
    stale_headers = {"Origin": "http://testserver", "X-CSRF-Token": b_claims.csrf_token, "X-Settings-Account": "a"}
    assert client.get("/api/v1/settings", headers={"X-Settings-Account": "a"}).status_code == 409
    assert client.patch("/api/v1/settings", json={"thinking_level": "extreme"}, headers=stale_headers).status_code == 409
    assert client.get("/api/v1/settings").json()["preferences"]["settings"]["thinking_level"] == "light"
    client.cookies.clear()
    client.cookies.set(auth.cookie_name, a_token)
    assert client.get("/api/v1/settings").json()["preferences"]["settings"]["thinking_level"] == "deep"
    client.close()
