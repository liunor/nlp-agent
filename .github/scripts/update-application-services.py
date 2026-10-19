"""Recreate application services after bootstrap succeeds; preserve MySQL/Redis."""
from __future__ import annotations

import argparse
import json
import subprocess
import time

APPLICATIONS = ("nova-web", "nova-worker", "nova-monitor", "nova-sandbox-manager", "nginx")


def require(condition, code):
    if not condition:
        raise ValueError(code)


def run(args):
    result = subprocess.run(args, capture_output=True, timeout=120)
    require(result.returncode == 0, "APPLICATION_COMPOSE_COMMAND_FAILED")
    return result.stdout


def inspect(name):
    return json.loads(run(["docker", "inspect", name]))[0]


def protected(project, read=inspect):
    result = {}
    for service in ("mysql", "redis"):
        value = read(project + "-" + service + "-1")
        require(value["State"]["Running"] and value["State"].get("Health", {}).get("Status") == "healthy"
                and value["Config"]["Labels"].get("com.docker.compose.project") == project,
                "EXISTING_DATABASE_SERVICE_NOT_HEALTHY")
        volumes = sorted(item["Name"] for item in value["Mounts"] if item.get("Type") == "volume")
        require(volumes == [project + "_" + service + "-data"], "EXISTING_DATABASE_VOLUME_CHANGED")
        result[service] = [value["Id"], value["Image"], value["State"]["StartedAt"],
                           value.get("RestartCount"), volumes]
    return result


def prepare_trigger_migrations(project, execute=run):
    # The password stays inside the existing MySQL container, never in argv
    # or captured output. Only the explicitly requested test environment is
    # configured automatically; production must be provisioned by its DBA.
    sql = "SELECT IF(@@GLOBAL.log_bin=0 OR @@GLOBAL.log_bin_trust_function_creators=1,1,0)"
    if project == "nova-test":
        sql = "SET GLOBAL log_bin_trust_function_creators=1; " + sql
    output = execute([
        "docker", "exec", project + "-mysql-1", "sh", "-ec",
        'export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; exec mysql --user=root '
        '--batch --skip-column-names --execute="' + sql + '"',
    ])
    require(output is not None and output.strip() in (b"1", "1"),
            "MYSQL_TRIGGER_MIGRATION_PRIVILEGES_REQUIRED")


def update(compose, project, *, execute=run, read=inspect, sleep=time.sleep, clock=time.monotonic,
           allowed_projects=("nova-test", "nova-prod"), prepare=prepare_trigger_migrations,
           worker_last=None):
    require(project in allowed_projects, "PROJECT_OUT_OF_SCOPE")
    original = protected(project, read)
    prepare(project, execute)
    # Explicitly exclude dependencies; an application release must not upgrade
    # the live database or Redis image/configuration as a side effect.
    execute([*compose, "up", "-d", "--no-deps", "--force-recreate", "--no-build", "nova-migrate"])
    deadline = clock() + 180
    while True:
        migration = read(project + "-nova-migrate-1")
        require(migration["Config"]["Labels"].get("com.docker.compose.project") == project,
                "MIGRATION_PROJECT_MISMATCH")
        if not migration["State"]["Running"]:
            require(migration["State"].get("ExitCode") == 0, "MIGRATION_FAILED_APPLICATIONS_UNCHANGED")
            break
        require(clock() < deadline, "MIGRATION_TIMEOUT_APPLICATIONS_UNCHANGED")
        sleep(1)
    require(protected(project, read) == original, "DATABASE_SERVICE_CHANGED_DURING_BOOTSTRAP")
    if worker_last is None:
        worker_last = project == "nova-test"
    groups = [APPLICATIONS]
    if worker_last:
        # Reserve all fixed application IPs before automatic Worker allocation.
        # Otherwise a new replica can take an address just freed by recreation.
        groups = [tuple(name for name in APPLICATIONS if name != "nova-worker"), ("nova-worker",)]
    for group in groups:
        execute([*compose, "up", "-d", "--no-deps", "--force-recreate", "--no-build", *group])
        require(protected(project, read) == original, "DATABASE_SERVICE_CHANGED_DURING_APPLICATION_UPDATE")
    return {"status": "APPLICATION_SERVICES_RECREATED_DATABASES_PRESERVED", "project": project,
            "migration_exit_code": 0, "mysql_recreated": False, "redis_recreated": False,
            "business_volumes_removed": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", choices=("nova-test", "nova-prod"), required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--override", required=True)
    args = parser.parse_args()
    compose = ["docker", "compose", "--profile", "monitor", "-p", args.project,
               "--env-file", args.env_file, "-f", "compose.yaml", "-f", args.override]
    print(json.dumps(update(compose, args.project)))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"status": "APPLICATION_UPDATE_STOPPED", "error_code":
                          str(error) if isinstance(error, ValueError) else type(error).__name__}))
        raise SystemExit(1)
