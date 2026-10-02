from datetime import datetime, timezone

from server.storage.monitoring import build_storage_snapshot, database_scope, deployment_environment


def test_storage_snapshot_is_scoped_to_the_current_environment() -> None:
    snapshot = build_storage_snapshot(
        environment="test",
        database={"name": "nlp_agent_test", "host": "mysql"},
        core_used_bytes=8,
        files_used_bytes=12,
        core_reserved_bytes=2,
        files_reserved_bytes=3,
        account_count=4,
        global_limit_bytes=100,
        disk_total_bytes=1_000,
        disk_free_bytes=400,
        minimum_free_bytes=100,
        now=datetime(2026, 9, 27, tzinfo=timezone.utc),
    )

    assert snapshot["scope"] == "current_environment_only"
    assert snapshot["environment"] == {"code": "test", "label": "测试"}
    assert snapshot["database"]["name"] == "nlp_agent_test"
    assert snapshot["account_pool"] == {
        "used_bytes": 20,
        "reserved_bytes": 5,
        "total_bytes": 25,
        "limit_bytes": 100,
        "available_bytes": 75,
        "ratio": 0.25,
        "state": "normal",
        "account_count": 4,
    }


def test_environment_fallback_uses_only_the_selected_database_name() -> None:
    class Settings:
        NLP_AGENT_DEPLOYMENT_ENV = ""
        NLP_AGENT_DATABASE_URL = "mysql+aiomysql://user:secret@mysql:3306/nlp_agent_test"

    assert deployment_environment(Settings()) == "test"
    assert database_scope(Settings.NLP_AGENT_DATABASE_URL) == {"name": "nlp_agent_test", "host": "mysql"}


def test_disk_is_reported_separately_from_account_pool() -> None:
    snapshot = build_storage_snapshot(
        environment="production",
        database={"name": "nlp_agent_prod", "host": "mysql"},
        core_used_bytes=40,
        files_used_bytes=20,
        core_reserved_bytes=0,
        files_reserved_bytes=0,
        account_count=1,
        global_limit_bytes=100,
        disk_total_bytes=1_000,
        disk_free_bytes=100,
        minimum_free_bytes=200,
    )

    assert snapshot["disk"]["state"] == "critical"
    assert snapshot["disk"]["shared_physical_disk"] is True
    assert snapshot["account_pool"]["total_bytes"] == 60
