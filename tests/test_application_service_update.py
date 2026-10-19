import copy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def updater():
    spec = importlib.util.spec_from_file_location("application_update", ROOT / ".github/scripts/update-application-services.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def services():
    result = {}
    for name in ("mysql", "redis", "nova-migrate"):
        result["nova-test-" + name + "-1"] = {
            "Id": name, "Image": "original", "RestartCount": 0,
            "State": {"Running": name != "nova-migrate", "Health": {"Status": "healthy"},
                      "StartedAt": "unchanged", "ExitCode": 0},
            "Config": {"Labels": {"com.docker.compose.project": "nova-test"}},
            "Mounts": [{"Type": "volume", "Name": "nova-test_" + name + "-data"}]}
    return result


def test_test_mysql_trigger_preflight_precedes_all_migration_commands(updater):
    values, commands = services(), []

    def execute(command):
        commands.append(command)
        return b"1\n"

    updater.update(["compose"], "nova-test", execute=execute, read=values.__getitem__)
    assert commands[0][:3] == ["docker", "exec", "nova-test-mysql-1"]
    assert "SET GLOBAL log_bin_trust_function_creators" in commands[0][-1]
    assert commands[1][-1] == "nova-migrate"


def test_trigger_preflight_failure_does_not_start_migration(updater):
    values, commands = services(), []

    def execute(command):
        commands.append(command)
        return b"0\n"

    with pytest.raises(ValueError, match="MYSQL_TRIGGER_MIGRATION_PRIVILEGES_REQUIRED"):
        updater.update(["compose"], "nova-test", execute=execute, read=values.__getitem__)
    assert len(commands) == 1


def test_production_trigger_preflight_is_read_only(updater):
    commands = []

    def execute(command):
        commands.append(command)
        return b"0\n"

    with pytest.raises(ValueError, match="MYSQL_TRIGGER_MIGRATION_PRIVILEGES_REQUIRED"):
        updater.prepare_trigger_migrations("nova-prod", execute)
    assert "SELECT IF" in commands[0][-1]
    assert "SET GLOBAL" not in commands[0][-1]


def test_bootstrap_is_completed_before_application_recreation(updater):
    values, commands = services(), []
    updater.update(["compose"], "nova-test", execute=commands.append, read=values.__getitem__, prepare=lambda *_: None)
    assert len(commands) == 3 and commands[0][-1] == "nova-migrate"
    assert commands[1][-4:] == [name for name in updater.APPLICATIONS if name != "nova-worker"]
    assert commands[2][-1] == "nova-worker"
    assert all("--no-deps" in command for command in commands)
    assert all("--remove-orphans" not in command and "--renew-anon-volumes" not in command for command in commands)
    assert all("mysql" not in command and "redis" not in command for command in commands)


def test_fixed_service_failure_never_recreates_workers(updater):
    values, commands = services(), []
    def execute(command):
        commands.append(command)
        if "nova-web" in command:
            raise ValueError("injected_fixed_service_failure")
    with pytest.raises(ValueError, match="injected_fixed_service_failure"):
        updater.update(["compose"], "nova-test", execute=execute, read=values.__getitem__, prepare=lambda *_: None)
    assert len(commands) == 2 and all("nova-worker" not in command for command in commands)


def test_database_change_after_fixed_service_update_stops_workers(updater):
    values, commands = services(), []
    def execute(command):
        commands.append(command)
        if "nova-web" in command:
            values["nova-test-mysql-1"]["Id"] = "changed"
    with pytest.raises(ValueError, match="DATABASE_SERVICE_CHANGED_DURING_APPLICATION_UPDATE"):
        updater.update(["compose"], "nova-test", execute=execute, read=values.__getitem__, prepare=lambda *_: None)
    assert len(commands) == 2 and all("nova-worker" not in command for command in commands)


def test_production_application_update_order_is_preserved(updater):
    original = services()
    values = {}
    for name, value in original.items():
        value["Config"]["Labels"]["com.docker.compose.project"] = "nova-prod"
        for mount in value["Mounts"]:
            mount["Name"] = mount["Name"].replace("nova-test_", "nova-prod_")
        values[name.replace("nova-test-", "nova-prod-")] = value
    commands = []
    updater.update(["compose"], "nova-prod", execute=commands.append, read=values.__getitem__, prepare=lambda *_: None)
    assert len(commands) == 2 and commands[1][-5:] == list(updater.APPLICATIONS)


def test_failed_migration_keeps_existing_applications_running(updater):
    values, commands = services(), []
    values["nova-test-nova-migrate-1"]["State"]["ExitCode"] = 7
    with pytest.raises(ValueError, match="MIGRATION_FAILED_APPLICATIONS_UNCHANGED"):
        updater.update(["compose"], "nova-test", execute=commands.append, read=values.__getitem__, prepare=lambda *_: None)
    assert len(commands) == 1 and commands[0][-1] == "nova-migrate"


def test_slow_migration_never_starts_new_applications(updater):
    values, commands = services(), []
    values["nova-test-nova-migrate-1"]["State"]["Running"] = True
    times = iter((0, 181))
    with pytest.raises(ValueError, match="MIGRATION_TIMEOUT_APPLICATIONS_UNCHANGED"):
        updater.update(["compose"], "nova-test", execute=commands.append, read=values.__getitem__, clock=lambda: next(times), prepare=lambda *_: None)
    assert len(commands) == 1


def test_database_replacement_during_bootstrap_stops_application_recreation(updater):
    values, commands = services(), []
    def execute(command):
        commands.append(command)
        values["nova-test-mysql-1"]["Id"] = "replaced"
    with pytest.raises(ValueError, match="DATABASE_SERVICE_CHANGED_DURING_BOOTSTRAP"):
        updater.update(["compose"], "nova-test", execute=execute, read=values.__getitem__, prepare=lambda *_: None)
    assert len(commands) == 1


def test_foreign_volume_is_rejected_before_any_update(updater):
    values, commands = copy.deepcopy(services()), []
    values["nova-test-mysql-1"]["Mounts"][0]["Name"] = "nova-prod_mysql-data"
    with pytest.raises(ValueError, match="EXISTING_DATABASE_VOLUME_CHANGED"):
        updater.update(["compose"], "nova-test", execute=commands.append, read=values.__getitem__)
    assert commands == []
