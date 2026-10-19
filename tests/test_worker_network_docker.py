"""Hosted CI only: actual Compose networking on uniquely owned dummy resources."""
from __future__ import annotations

import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
OWNER = "nlp-agent.worker-network-probe"
pytestmark = pytest.mark.skipif(
    os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUN_WORKER_NETWORK_DOCKER") != "1",
    reason="requires explicit opt-in on a disposable GitHub-hosted runner",
)


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / ".github/scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(command):
    result = subprocess.run(command, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stderr.decode(errors="replace")[-3000:]
    return result.stdout


def inspect(kind, target):
    return json.loads(run(["docker", kind, "inspect", target]))[0]


def cleanup(project, nonce):
    assert project == "worker-net-probe-" + nonce and len(nonce) == 12
    identifiers = run(["docker", "ps", "-aq", "--no-trunc", "--filter", f"label={OWNER}={nonce}"]).decode().split()
    for identifier in identifiers:
        value = inspect("container", identifier)
        labels = value["Config"]["Labels"]
        assert labels.get(OWNER) == nonce and labels.get("com.docker.compose.project") == project
        assert value["Name"].startswith("/" + project + "-")
        run(["docker", "rm", "-f", value["Id"]])
    # No prune/down -v: inspect every exact, newly owned name before removal.
    for suffix in ("mysql-data", "redis-data"):
        name = project + "_" + suffix
        result = subprocess.run(["docker", "volume", "inspect", name], capture_output=True, timeout=15)
        if result.returncode == 0:
            value = json.loads(result.stdout)[0]
            assert value["Labels"].get(OWNER) == nonce
            assert value["Labels"].get("com.docker.compose.project") == project
            run(["docker", "volume", "rm", name])
    name = project + "_default"
    result = subprocess.run(["docker", "network", "inspect", name], capture_output=True, timeout=15)
    if result.returncode == 0:
        value = json.loads(result.stdout)[0]
        assert value["Labels"].get(OWNER) == nonce and not value["Containers"]
        run(["docker", "network", "rm", name])


def test_compose_worker_replica_update_and_rollback(tmp_path):
    updater = load_script("update-application-services")
    release = load_script("prepare-auth-release")
    nonce = uuid.uuid4().hex[:12]
    project = "worker-net-probe-" + nonce
    net_name = project + "_default"
    labels = {OWNER: nonce}
    image = "alpine:3.21"
    path = tmp_path / "compose.json"
    compose = ["docker", "compose", "-p", project, "-f", str(path)]
    run(["docker", "pull", "--quiet", image])
    run(["docker", "network", "create", "--internal", "--label", f"{OWNER}={nonce}", net_name])
    try:
        network = inspect("network", net_name)
        subnet = ipaddress.ip_network(network["IPAM"]["Config"][0]["Subnet"])
        # Let Docker choose an unused pool, then explicitly declare that same
        # subnet. Automatic-pool networks reject user-specified static IPs.
        assert network["Labels"].get(OWNER) == nonce and not network["Containers"]
        gateway = network["IPAM"]["Config"][0]["Gateway"]
        run(["docker", "network", "rm", net_name])
        run(["docker", "network", "create", "--internal", "--subnet", str(subnet),
             "--gateway", gateway, "--label", f"{OWNER}={nonce}", net_name])
        addresses = {name: str(subnet.network_address + index) for index, name in enumerate(
            ("nova-web", "nova-monitor", "nova-sandbox-manager", "nginx", "mysql", "redis", "nova-worker"), start=4)}

        def write(revision, *, dynamic=False, replicas=1, migration_exit=0):
            def service(name):
                value = {"image": image, "command": ["sleep", "600"], "labels": labels,
                         "mem_limit": "32m", "cpus": 0.1, "pids_limit": 16,
                         "environment": {"PROBE_REVISION": revision},
                         "networks": {"default": {"ipv4_address": addresses[name]}}}
                if name in ("mysql", "redis"):
                    value["volumes"] = [name + "-data:/probe-data"]
                    value["environment"] = {"PROBE_REVISION": "unchanged-infrastructure"}
                    value["healthcheck"] = {"test": ["CMD", "true"], "interval": "1s", "timeout": "1s", "retries": 20}
                return value
            services = {name: service(name) for name in addresses}
            services["nova-worker"]["deploy"] = {"replicas": replicas}
            if dynamic:
                services["nova-worker"]["networks"] = {"default": {}}
            services["nova-migrate"] = {"image": image, "command": ["sh", "-c", f"exit {migration_exit}"],
                                        "labels": labels, "network_mode": "none"}
            config = {"services": services,
                      "networks": {"default": {"name": net_name, "external": True}},
                      "volumes": {name: {"labels": labels} for name in ("mysql-data", "redis-data")}}
            path.write_text(json.dumps(config), encoding="utf-8")

        def read(name):
            return inspect("container", name)

        def update():
            return updater.update(compose, project, execute=run, read=read, allowed_projects=(project,),
                                  prepare=lambda *_: None, worker_last=True)

        def worker_instances():
            return release.workers(project)

        def app_ids():
            return {name: read(project + "-" + name + "-1")["Id"] for name in updater.APPLICATIONS}

        def assert_fixed():
            for service, address in addresses.items():
                if service != "nova-worker":
                    assert read(project + "-" + service + "-1")["NetworkSettings"]["Networks"][net_name]["IPAddress"] == address

        write("original")
        run([*compose, "up", "-d", "--wait", "--no-build", "mysql", "redis"])
        update()
        infrastructure = updater.protected(project, read)
        run(["docker", "exec", project + "-mysql-1", "sh", "-c", "printf preserved > /probe-data/sentinel"])

        # Reproduce the old configuration's actual Engine address collision.
        failed = subprocess.run([*compose, "up", "-d", "--no-deps", "--scale", "nova-worker=2", "nova-worker"],
                                capture_output=True, timeout=90)
        assert failed.returncode != 0
        assert b"address already in use" in failed.stderr.lower(), failed.stderr.decode(errors="replace")[-2000:]
        unexpected = subprocess.run(["docker", "inspect", project + "-nova-worker-2"], capture_output=True, timeout=15)
        if unexpected.returncode == 0:
            value = json.loads(unexpected.stdout)[0]
            assert value["Config"]["Labels"].get(OWNER) == nonce
            assert not value["State"]["Running"]
            run(["docker", "rm", value["Id"]])
        assert len(worker_instances()) == 1

        write("new", dynamic=True, replicas=2)
        update()
        instances = worker_instances()
        assert len(instances) == 2 and all(item["State"]["Running"] for item in instances)
        worker_ips = {item["NetworkSettings"]["Networks"][net_name]["IPAddress"] for item in instances}
        assert len(worker_ips) == 2 and not worker_ips.intersection(
            address for name, address in addresses.items() if name != "nova-worker")
        assert_fixed()
        assert updater.protected(project, read) == infrastructure

        # A later release must retain the observed two replicas.
        write("second-release", dynamic=True, replicas=len(instances))
        update()
        assert len(worker_instances()) == 2
        assert all("PROBE_REVISION=second-release" in item["Config"]["Env"] for item in worker_instances())
        assert_fixed()

        write("broken", dynamic=True, replicas=2, migration_exit=7)
        previous = app_ids()
        with pytest.raises(ValueError, match="MIGRATION_FAILED_APPLICATIONS_UNCHANGED"):
            update()
        assert app_ids() == previous and len(worker_instances()) == 2

        write("original")
        update()
        restored = worker_instances()
        assert len(restored) == 1 and restored[0]["State"]["Running"]
        assert restored[0]["NetworkSettings"]["Networks"][net_name]["IPAddress"] == addresses["nova-worker"]
        assert all("PROBE_REVISION=original" in read(project + "-" + name + "-1")["Config"]["Env"] for name in updater.APPLICATIONS)
        assert_fixed()
        assert updater.protected(project, read) == infrastructure
        assert run(["docker", "exec", project + "-mysql-1", "cat", "/probe-data/sentinel"]) == b"preserved"
        print(json.dumps({"status": "ISOLATED_WORKER_NETWORK_UPDATE_AND_ROLLBACK_PASSED",
                          "old_fixed_ip_collision_reproduced": True, "two_distinct_worker_addresses": True,
                          "replica_count_survives_next_release": True, "fixed_proxy_ips_preserved": True,
                          "failed_migration_kept_old_apps_running": True, "original_static_configuration_restored": True,
                          "dummy_database_containers_and_volume_sentinel_preserved": True,
                          "business_volumes_mounted": False, "production_services_changed": False}))
    finally:
        cleanup(project, nonce)
