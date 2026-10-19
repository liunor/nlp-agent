"""Keep reported pool budgets aligned with the real child Settings parser."""
import pytest
import json
from types import SimpleNamespace
from pathlib import Path
from copy import deepcopy
import os
import subprocess
import sys

from configs.settings import Settings
from tests.load.run_isolated import WEB_POOLS, WORKER_POOLS, measured_profile


def test_all_bounded_fixture_names_match_profile_and_real_user_schema():
    from server.user.schemas import UserCreate
    from tests.load.run_isolated import student_username
    profile = json.loads((Path(__file__).parent / "load/profiles/t07-50-users.json").read_text())
    names = []
    for index in range(1, 121):
        name = student_username(index)
        assert profile["username_format"].format(index=index) == name
        assert UserCreate(username=name, display_name="Fixture", password="isolated-password-123").username == name
        names.append(name)
    assert len(set(names)) == 120


@pytest.mark.parametrize("flag,origin,kind,users,classes,accepted", [
    (False, "https://test.example", "regression", 50, 1, True),
    (True, "http://127.0.0.1:8765", "regression", 120, 3, True),
    (False, "http://127.0.0.1:8765", "regression", 120, 3, False),
    (True, "https://test.lsnunlp.com", "regression", 120, 3, False),
    (True, "http://127.0.0.1:8765", "baseline", 120, 3, False),
    (True, "http://127.0.0.1:8765", "regression", 121, 3, False),
    (True, "http://127.0.0.1:8765", "regression", 120, 1, False),
])
def test_three_class_mode_cannot_expand_the_live_or_frozen_release_gate(flag, origin, kind, users, classes, accepted):
    from tests.load.t07_classroom_load import validate_workload_size, LoadTestError
    args = SimpleNamespace(isolated_three_class_comparison=flag, run_kind=kind)
    profile = {"user_count": users, "turn_count": 30, "classroom_count": classes}
    if accepted:
        validate_workload_size(profile, args, origin)
    else:
        with pytest.raises(LoadTestError, match="profile requires"):
            validate_workload_size(profile, args, origin)


def test_fake_model_configuration_validates_actual_catalog_without_native_search():
    # A fresh process avoids replacing the suite's process-global Settings/model
    # runtime. Creating SQLAlchemy pools here is lazy and opens no DB connection.
    code = '''
from tests.load.model_configuration import configure_models
from configs.settings import settings
configure_models()
assert all(v['adapter'] == 'openai_compatible' for v in settings._config['providers'].values())
assert all(not v['native_search']['enabled'] for v in settings._config['model_presets'].values())
print('FAKE_CATALOG_VALIDATED')
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parents[1],
                            env={**os.environ,
                                 "NLP_AGENT_DATABASE_URL": "mysql+aiomysql://fixture:fixture@127.0.0.1:1/nlp_agent_api_http_only",
                                 "NLP_AGENT_DEPLOYMENT_ENV": "test",
                                 "T07_FAKE_PROVIDER_CONTROL_URL": "http://127.0.0.1:18767"},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'FAKE_CATALOG_VALIDATED' in result.stdout


@pytest.mark.parametrize("variable,value", [
    ("NLP_AGENT_DEPLOYMENT_ENV", "production"),
    ("NLP_AGENT_DATABASE_URL", "mysql+aiomysql://u:p@mysql/nlp_agent_api_http_only"),
    ("NLP_AGENT_DATABASE_URL", "mysql+aiomysql://u:p@127.0.0.1/nlp_agent"),
    ("T07_FAKE_PROVIDER_CONTROL_URL", "http://provider.example"),
    ("T07_FAKE_PROVIDER_CONTROL_URL", "http://u:p@127.0.0.1:8767"),
    ("T07_FAKE_PROVIDER_CONTROL_URL", "http://127.0.0.1:8767/unapproved"),
])
def test_fake_configuration_rejects_foreign_targets_before_editing_settings(monkeypatch, variable, value):
    from configs.settings import settings
    from tests.load.model_configuration import configure_models
    before = deepcopy(settings._config)
    monkeypatch.setenv("NLP_AGENT_DATABASE_URL", "mysql+aiomysql://u:p@127.0.0.1/nlp_agent_api_http_only")
    monkeypatch.setenv("NLP_AGENT_DEPLOYMENT_ENV", "test")
    monkeypatch.setenv("T07_FAKE_PROVIDER_CONTROL_URL", "http://127.0.0.1:8767")
    monkeypatch.setenv(variable, value)
    with pytest.raises(RuntimeError, match="disposable loopback"):
        configure_models()
    assert settings._config == before


@pytest.mark.parametrize("replicas,concurrency", [(1, 1), (1, 2), (2, 2), (2, 4)])
def test_capacity_profile_matches_effective_settings_and_leaves_parent_untouched(replicas, concurrency):
    web_env = dict(WEB_POOLS)
    worker_env = dict(WORKER_POOLS, NLP_AGENT_WORKER_CONCURRENCY=str(concurrency))
    web, worker = Settings(_env_file=None, **web_env), Settings(_env_file=None, **worker_env)
    original = {"database_pool_size": 99, "worker_database_pool_size": 99, "version": 1}
    result = measured_profile(original, web_env, worker_env, replicas=replicas, concurrency=concurrency)
    assert result["database_pool_size"] == web.database_runtime["pool_size"] == 5
    assert result["database_max_overflow"] == web.database_runtime["max_overflow"] == 5
    assert result["database_sync_pool_size"] == web.database_runtime["sync_pool_size"] == 5
    assert result["worker_database_pool_size"] == worker.database_runtime["pool_size"] == 2
    assert result["worker_database_max_overflow"] == worker.database_runtime["max_overflow"] == 1
    assert result["worker_sync_pool_size"] == worker.database_runtime["sync_pool_size"] == 4
    assert result["worker_sync_max_overflow"] == worker.database_runtime["sync_max_overflow"] == 2
    assert result["database_pool_timeout_seconds"] == worker.database_runtime["pool_timeout_s"] == 5
    assert result["worker_replicas"] == replicas
    assert result["worker_concurrency"] == worker.NLP_AGENT_WORKER_CONCURRENCY == concurrency
    assert original["database_pool_size"] == original["worker_database_pool_size"] == 99


def test_baseline_reports_supplied_fixture_pools_instead_of_claiming_vps_values():
    env = {"NLP_AGENT_DB_POOL_SIZE": "3", "NLP_AGENT_DB_MAX_OVERFLOW": "3"}
    result = measured_profile({}, env, env, replicas=1, concurrency=1)
    assert result["database_pool_size"] == result["worker_database_pool_size"] == 3
    assert result["database_max_overflow"] == result["worker_database_max_overflow"] == 3


@pytest.mark.parametrize("gauges,exited,accepted", [
    ([{"concurrency": 2}, {"concurrency": 2}], False, True),
    ([{"concurrency": 2}], False, False),
    ([{"concurrency": 2}, {"concurrency": 4}], False, False),
    ([{"concurrency": 2}, {"concurrency": 2}], True, False),
])
def test_topology_uses_live_matching_heartbeats_and_running_processes(monkeypatch, gauges, exited, accepted):
    import redis
    from tests.load import run_isolated as runner

    closed = []
    client = SimpleNamespace(scan_iter=lambda **_: range(len(gauges)),
                             get=lambda key: json.dumps(gauges[key]), close=lambda: closed.append(True))
    monkeypatch.setattr(redis, "Redis", lambda **_: client)
    clock = iter([0, 0, 46])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    environment = SimpleNamespace(redis_port=12345, redis_db=15,
                                  capacity_workers=[SimpleNamespace(poll=lambda: 1 if exited else None)])
    if accepted:
        assert runner.verify_worker_topology(environment, 2, 2) == {
            "observed_replicas": 2, "concurrency_per_replica": 2}
    else:
        with pytest.raises(RuntimeError, match="ISOLATED_"):
            runner.verify_worker_topology(environment, 2, 2)
    assert closed == [True]
