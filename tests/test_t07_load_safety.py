from __future__ import annotations

from pathlib import Path

import pytest

from tests.load.t07_classroom_load import LoadTestError, validate_base_url, validate_output_path


def test_load_runner_accepts_only_absolute_http_targets() -> None:
    assert validate_base_url("http://127.0.0.1:8765/") == "http://127.0.0.1:8765"
    with pytest.raises(LoadTestError, match="absolute http"):
        validate_base_url("file:///tmp/production")


@pytest.mark.parametrize(
    "url",
    [
        "https://api-production.example.test",
        "https://live.example.test",
        "http://user:password@127.0.0.1:8765",
    ],
)
def test_load_runner_rejects_production_hosts_and_embedded_credentials(url: str) -> None:
    with pytest.raises(LoadTestError):
        validate_base_url(url)


def test_load_runner_refuses_report_overwrite_and_paths_outside_t07_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.load import t07_classroom_load

    monkeypatch.setattr(t07_classroom_load, "ROOT", tmp_path)
    allowed = Path("artifacts/load-tests/t07/baseline.json")
    assert validate_output_path(str(allowed)).name == "baseline.json"

    outside = tmp_path / "baseline.json"
    with pytest.raises(LoadTestError, match="inside artifacts"):
        validate_output_path(str(outside))

    existing = tmp_path / "artifacts" / "load-tests" / "t07" / "baseline.json"
    existing.parent.mkdir(parents=True)
    existing.write_text("keep", encoding="utf-8")
    with pytest.raises(LoadTestError, match="overwrite"):
        validate_output_path(str(existing))


def test_baseline_json_requires_a_new_summary_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.load import t07_classroom_load

    monkeypatch.setattr(t07_classroom_load, "ROOT", tmp_path)
    report = tmp_path / "artifacts" / "load-tests" / "t07" / "baseline.json"
    report.parent.mkdir(parents=True)
    report.with_name("baseline-summary.md").write_text("keep", encoding="utf-8")

    with pytest.raises(LoadTestError, match="baseline-summary"):
        validate_output_path(str(report))


def test_current_regression_cannot_be_named_pre_fix_baseline():
    from argparse import Namespace
    from tests.load.t07_classroom_load import validate_run_kind

    with pytest.raises(LoadTestError, match="isolated old version"):
        validate_run_kind(Namespace(run_kind="regression"), Path("baseline.json"))


def test_baseline_requires_frozen_ref_and_unchanged_product_code(monkeypatch):
    from argparse import Namespace
    from subprocess import CompletedProcess
    from unittest.mock import Mock
    from tests.load import t07_classroom_load as load

    reference = load.BASELINE_PRODUCT_COMMIT
    mocked = Mock(side_effect=[
        CompletedProcess([], 0, reference + "\n"),
        CompletedProcess([], 0, reference + "\n"),
        CompletedProcess([], 0, " M gateway/core.py\n"),
    ])
    monkeypatch.setattr(load.subprocess, "run", mocked)
    with pytest.raises(LoadTestError, match="unchanged product code"):
        load.validate_run_kind(Namespace(run_kind="baseline", baseline_ref=reference), Path("baseline.json"))


@pytest.mark.asyncio
async def test_baseline_rejects_regression_target_even_from_frozen_checkout(tmp_path, monkeypatch):
    import json
    from argparse import Namespace
    from unittest.mock import AsyncMock
    import httpx
    from tests.load import t07_classroom_load as load

    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"version": 1, "scenarios": [], "user_count": 50, "turn_count": 30}), encoding="utf-8")
    monkeypatch.setattr(load, "ROOT", tmp_path)
    monkeypatch.setattr(load, "validate_run_kind", lambda *_args: None)  # frozen local product already validated
    monkeypatch.setattr(load, "_profile_users", lambda _profile: [{} for _ in range(50)])
    monkeypatch.setattr(load, "_fake_provider_control", AsyncMock(side_effect=AssertionError("must reject the wrong target before touching Provider control")))
    client = httpx.AsyncClient(base_url="http://127.0.0.1:8765", transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ready": True, "deployment_environment": "test"})))
    monkeypatch.setattr(load.httpx, "AsyncClient", lambda **_kwargs: client)
    args = Namespace(base_url="http://127.0.0.1:8765", output="artifacts/load-tests/t07/baseline.json", profile=str(profile), run_kind="baseline")
    with pytest.raises(LoadTestError, match="target.*commit"):
        await load.run(args)


def test_classroom_fixture_and_load_login_use_the_same_50_valid_identities(monkeypatch):
    import json
    from tests.load.run_isolated import classroom_user_data, ROOT, TEST_PASSWORD
    from tests.load.t07_classroom_load import _profile_users
    from server.user.schemas import UserCreate

    profile = json.loads((ROOT / "tests/load/profiles/t07-50-users.json").read_text(encoding="utf-8"))
    monkeypatch.setenv(profile["password_env"], TEST_PASSWORD)
    students = classroom_user_data(profile)
    logins = _profile_users(profile)

    assert len(students) == len({user.username for user in students}) == 50
    assert all(isinstance(user, UserCreate) for user in students)
    assert [user.username for user in students] == [login["username"] for login in logins]
    assert all(user.password == login["password"] for user, login in zip(students, logins, strict=True))


@pytest.mark.asyncio
async def test_invalid_classroom_fixture_stops_before_database_access(monkeypatch):
    from unittest.mock import Mock
    from pydantic import ValidationError
    from server.infrastructure.mysql import MySQLRuntime
    from tests.load.run_isolated import seed_classroom

    database = Mock(side_effect=AssertionError("invalid fixture must not open a database"))
    monkeypatch.setattr(MySQLRuntime, "from_runtime", database)
    with pytest.raises(ValidationError, match="alphanumeric"):
        await seed_classroom(object(), {
            "user_count": 50, "username_format": "t07-student-{index:02d}",
        })
    database.assert_not_called()


@pytest.mark.parametrize("worker,sync_size", [(False, 5), (True, 4), (True, 8)])
def test_isolated_database_settings_match_the_report_profile(worker, sync_size, monkeypatch):
    import json
    from configs.settings import Settings
    from tests.load.run_isolated import database_environment, ROOT

    profile = json.loads((ROOT / "tests/load/profiles/t07-50-users.json").read_text(encoding="utf-8"))
    profile["worker_sync_pool_size" if worker else "database_sync_pool_size"] = sync_size
    for key, value in database_environment(profile, worker=worker).items():
        monkeypatch.setenv(key, value)
    runtime = Settings(_env_file=None).database_runtime
    prefix = "worker_" if worker else ""
    sync_prefix = "worker_sync_" if worker else "database_sync_"
    assert runtime["pool_size"] == profile[f"{prefix}database_pool_size"]
    assert runtime["max_overflow"] == profile[f"{prefix}database_max_overflow"]
    assert runtime["sync_pool_size"] == profile[f"{sync_prefix}pool_size"]
    assert runtime["sync_max_overflow"] == profile[f"{sync_prefix}max_overflow"]
    assert runtime["pool_timeout_s"] == profile["database_pool_timeout_seconds"]


def test_frozen_baseline_rejects_changed_worker_pool_before_startup(monkeypatch):
    import sys
    from unittest.mock import Mock
    from tests.load import run_isolated

    resources = Mock(side_effect=AssertionError("must reject baseline tuning before starting resources"))
    monkeypatch.setattr(run_isolated, "FakeProviderServer", resources)
    monkeypatch.setattr(sys, "argv", ["run_isolated", "--run-kind", "baseline", "--worker-sync-pool-size", "8",
                                     "--output", "artifacts/load-tests/t07/baseline.json"])
    with pytest.raises(SystemExit) as stopped:
        run_isolated.main()
    assert stopped.value.code == 2
    resources.assert_not_called()


def test_fake_provider_configuration_validates_real_model_contract(monkeypatch):
    from copy import deepcopy
    from configs.settings import settings
    from core.model_runtime.factory import ModelFactory
    from tests.load.model_configuration import configure_models

    raw = deepcopy(settings._config)
    assert raw["model_presets"]["worker-qwen-web"]["native_search"]["enabled"]
    monkeypatch.setattr(settings, "_config", raw)
    monkeypatch.setenv("NLP_AGENT_DEPLOYMENT_ENV", "test")
    monkeypatch.setenv("NLP_AGENT_DATABASE_URL", "mysql+aiomysql://isolated:unused@127.0.0.1:33061/nlp_agent_api_http_isolated")
    monkeypatch.setenv("T07_FAKE_PROVIDER_CONTROL_URL", "http://127.0.0.1:33221")
    configure_models()
    config = ModelFactory.config_from_raw(raw)
    assert all(provider.base_url == "http://127.0.0.1:33221/v1" for provider in config.providers.values())
    assert all(not preset.native_search.enabled for preset in config.model_presets.values())


@pytest.mark.parametrize("database,endpoint", [
    ("mysql+aiomysql://unused@mysql/nlp_agent", "http://127.0.0.1:33221"),
    ("mysql+aiomysql://unused@127.0.0.1/nlp_agent", "http://127.0.0.1:33221"),
    ("mysql+aiomysql://unused@127.0.0.1/nlp_agent_api_http_isolated", "https://api.example.test"),
])
def test_fake_provider_configuration_rejects_live_targets_without_mutation(monkeypatch, database, endpoint):
    from copy import deepcopy
    from configs.settings import settings
    from tests.load.model_configuration import configure_models

    before = deepcopy(settings._config)
    monkeypatch.setenv("NLP_AGENT_DEPLOYMENT_ENV", "test")
    monkeypatch.setenv("NLP_AGENT_DATABASE_URL", database)
    monkeypatch.setenv("T07_FAKE_PROVIDER_CONTROL_URL", endpoint)
    with pytest.raises(RuntimeError, match="disposable loopback"):
        configure_models()
    assert settings._config == before


@pytest.mark.asyncio
@pytest.mark.parametrize("timestamp,expected_ms", [
    ("2026-10-18T00:00:01", 1000),
    ("2026-10-18T00:00:01Z", 1000),
    ("2026-10-18T08:00:01+08:00", 1000),
    ("invalid", None),
])
async def test_completed_turn_metrics_preserve_naive_utc_events(monkeypatch, timestamp, expected_ms):
    from datetime import datetime, timezone
    import httpx
    from tests.load import t07_classroom_load as load

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 18, tzinfo=timezone.utc)

    monkeypatch.setattr(load, "datetime", FixedDateTime)
    def reply(request):
        if request.method == "POST":
            return httpx.Response(202, json={"turn_id": "isolated-turn", "status": "accepted"})
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json={"items": [
                {"sequence": 1, "type": "message.delta", "created_at": timestamp},
                {"sequence": 2, "type": "turn.completed"},
            ]})
        return httpx.Response(200, json={"status": "completed", "final_text": "isolated response"})

    async with httpx.AsyncClient(base_url="http://127.0.0.1", transport=httpx.MockTransport(reply)) as client:
        result = await load._submit_and_wait({"client": client, "origin": "http://127.0.0.1", "csrf": "isolated", "session_id": "isolated-session", "username": "isolated", "index": 1},
            run_id="isolated", timeout_s=1, ramp_seconds=0, index=0, total=1, start_at=0)
    assert result["submit_status"] == 202 and result["state"] == "completed"
    assert result["terminal_event_count"] == 1 and not result["event_sequence_gap"]
    assert result["first_token_ms"] == expected_ms


def test_timeout_scenario_allows_the_turn_watchdog_but_still_requires_live_session():
    from tests.load.t07_classroom_load import provider_case_failures
    result = {"submit_status": 202, "state": "failed", "error_kind": "TurnExecutionTimeoutError", "terminal_event_count": 1, "event_sequence_gap": False, "session_after_provider_error_status": 200}
    assert provider_case_failures("timeout", result) == []
    result["session_after_provider_error_status"] = 401
    assert provider_case_failures("timeout", result) == ["timeout: browser auth session was lost"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error_name", ["RemoteProtocolError", "ConnectTimeout", "ConnectError"])
async def test_ticket_transport_failure_is_recorded_before_websocket_without_retry_or_secrets(monkeypatch, error_name):
    from unittest.mock import AsyncMock
    import httpx
    from tests.load import t07_classroom_load as load

    calls = []

    def reply(request):
        calls.append(request.url.path)
        raise getattr(httpx, error_name)("do-not-record Cookie=private-cookie ticket=private-ticket")

    connect = AsyncMock(side_effect=AssertionError("WebSocket must not start without a ticket"))
    monkeypatch.setattr(load, "connect", connect)
    async with httpx.AsyncClient(base_url="http://127.0.0.1", transport=httpx.MockTransport(reply)) as client:
        connection, result = await load._open_websocket({"username": "synthetic", "client": client, "csrf": "private-csrf"},
                                                       base_url="http://127.0.0.1", origin="http://127.0.0.1")
    assert connection is None and result["status"] == "error"
    assert result["stage"] == "ws_ticket_http" and result["error"] == error_name
    assert calls == ["/api/v1/auth/ws-ticket"]
    assert "private-" not in str(result)
    connect.assert_not_called()
