"""Keep reported pool budgets aligned with the real child Settings parser."""
import pytest
import json
from types import SimpleNamespace
from pathlib import Path

from configs.settings import Settings
from tests.load.run_isolated import WEB_POOLS, WORKER_POOLS, measured_profile


def test_all_fifty_fixture_names_match_profile_and_real_user_schema():
    from server.user.schemas import UserCreate
    from tests.load.run_isolated import student_username
    profile = json.loads((Path(__file__).parent / "load/profiles/t07-50-users.json").read_text())
    names = []
    for index in range(1, 51):
        name = student_username(index)
        assert profile["username_format"].format(index=index) == name
        assert UserCreate(username=name, display_name="Fixture", password="isolated-password-123").username == name
        names.append(name)
    assert len(set(names)) == 50


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
