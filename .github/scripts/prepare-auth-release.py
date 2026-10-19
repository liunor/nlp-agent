"""Prepare a scoped Compose overlay from the audited live proxy chain.

preview reads only. prepare writes release-owned configuration files outside
the Actions checkout. stage adds immutable frontend selection after image pull.
This helper never starts/stops business containers or restores a database.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request

HOST_CONFIG = Path("/etc/nginx/conf.d/lsnunlp.conf")
HOST_SHA = "46398d92118a55d1fc58eb70bed12f7d3d049760130d3839483caf424c53140f"
PROJECTS = {"nova-test": 18765, "nova-prod": 8765}
LIMIT_DEFAULTS = {"NLP_AGENT_AUTH_MAX_LOGIN_ATTEMPTS": 5, "NLP_AGENT_AUTH_MAX_IP_LOGIN_ATTEMPTS": 100,
                  "NLP_AGENT_AUTH_RATE_WINDOW_S": 900, "NLP_AGENT_AUTH_IP_RATE_WINDOW_S": 900}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def run(args, *, data=None):
    result = subprocess.run(args, input=data, capture_output=True, timeout=120)
    require(result.returncode == 0, "DOCKER_COMMAND_FAILED_REVIEW_HOST_PRIVATELY")
    return result.stdout


def inspect(name):
    return json.loads(run(["docker", "inspect", name]))[0]


def workers(project):
    identifiers = run(["docker", "ps", "-aq", "--no-trunc", "--filter",
                       "label=com.docker.compose.project=" + project, "--filter",
                       "label=com.docker.compose.service=nova-worker"]).decode().split()
    require(1 <= len(identifiers) <= 2, "WORKER_REPLICA_COUNT_REQUIRES_REVIEW")
    return [inspect(identifier) for identifier in identifiers]


def worker_snapshot(project, instances, subnet, occupied):
    """Audit all test replicas, including stopped containers; never silently scale down."""
    require(project == "nova-test" and 1 <= len(instances) <= 2,
            "WORKER_REPLICA_COUNT_REQUIRES_REVIEW")
    identifiers, numbers, addresses = set(), set(), set(occupied)
    for instance in instances:
        labels = instance["Config"]["Labels"]
        number = labels.get("com.docker.compose.container-number")
        require(instance["State"]["Running"]
                and labels.get("com.docker.compose.project") == project
                and labels.get("com.docker.compose.service") == "nova-worker"
                and labels.get("com.docker.compose.oneoff", "False").lower() == "false"
                and isinstance(number, str) and number.isascii() and number.isdigit()
                and 1 <= int(number) <= 2147483647 and number not in numbers
                and instance["Id"] not in identifiers
                and set(instance["NetworkSettings"]["Networks"]) == {project + "_default"}
                and not instance["HostConfig"].get("PortBindings"),
                "WORKER_INSTANCE_REQUIRES_REVIEW")
        address = instance["NetworkSettings"]["Networks"][project + "_default"]["IPAddress"]
        require(ipaddress.ip_address(address) in subnet and address not in addresses,
                "WORKER_IP_REQUIRES_REVIEW")
        identifiers.add(instance["Id"])
        numbers.add(number)
        addresses.add(address)
    return sorted(identifiers)


def release_identity(metadata):
    identity = {"project": metadata["project"], "image": metadata["image"],
                "limits": metadata["limits"], "web_id": metadata["web_id"],
                "nginx_id": metadata["nginx_id"], "fixed_ips": metadata["fixed_ips"],
                "files": metadata["config_hashes"]}
    # Legacy prepared releases retain their exact identity and remain readable.
    if "worker_ids" in metadata:
        identity["worker_ids"] = metadata["worker_ids"]
        identity["worker_subnet"] = metadata["worker_subnet"]
    return sha(json.dumps(identity, sort_keys=True).encode())


def sha(data):
    return hashlib.sha256(data).hexdigest()


def guard(path):
    require(path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)),
            "UNEXPECTED_OR_SYMLINK_RELEASE_PATH")


def immutable(path, data):
    guard(path)
    if path.exists():
        require(path.read_bytes() == data, "EXISTING_RELEASE_FILE_CHANGED")
        return
    with path.open("xb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    os.chmod(path, 0o600)


def proxy_config(source):
    # Same bounded transformation already exercised with real nginx in the
    # client-IP fix. Preserve all unrelated routes and settings from this host.
    if "nova_trusted_edge" in source:
        require("include /etc/nginx/trusted-edge-http.conf;" in source
                and "include /etc/nginx/trusted-edge-addresses.conf;" in source
                and "real_ip_recursive on;" in source
                and "proxy_set_header X-Forwarded-For $remote_addr;" in source
                and "proxy_set_header X-Forwarded-Proto $nova_forwarded_proto;" in source
                and "$proxy_add_x_forwarded_for" not in source
                and "proxy_set_header X-Forwarded-Proto $scheme;" not in source,
                "UNRECOGNIZED_PARTIAL_PROXY_CONFIGURATION")
        return source
    anchor = "    log_format main "
    require(source.count(anchor) == 1
            and source.count("proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;") > 0
            and source.count("proxy_set_header X-Forwarded-Proto $scheme;") > 0
            and "set_real_ip_from" not in source and "real_ip_header" not in source,
            "UNRECOGNIZED_PROXY_CONFIGURATION")
    block = '''    include /etc/nginx/trusted-edge-http.conf;
    real_ip_header X-Forwarded-For;
    real_ip_recursive on;
    geo $realip_remote_addr $nova_trusted_edge {
        default 0;
        include /etc/nginx/trusted-edge-addresses.conf;
    }
    map "$nova_trusted_edge:$http_x_forwarded_proto" $nova_forwarded_proto {
        default $scheme;
        "1:https" https;
        "1:http" http;
    }

'''
    return source.replace(anchor, block + anchor).replace(
        "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "proxy_set_header X-Forwarded-For $remote_addr;").replace(
        "proxy_set_header X-Forwarded-Proto $scheme;",
        "proxy_set_header X-Forwarded-Proto $nova_forwarded_proto;")


def _classroom_rate_policy(source, project):
    """Test-only NAT burst allowance, still bounded per IP and per server."""
    if project != "nova-test":
        return source
    marker = "# nova-test classroom edge policy v1"
    old_zone = "    limit_req_zone $binary_remote_addr zone=api_limit:10m rate=30r/s;"
    old_limit = "limit_req zone=api_limit burst=60 nodelay;"
    new_zone = "    limit_req_zone $binary_remote_addr zone=api_limit:10m rate=120r/s;"
    new_limit = "limit_req zone=api_limit burst=240 nodelay;"
    global_zone = "    limit_req_zone $server_name zone=classroom_server_limit:10m rate=120r/s;"
    global_limit = "limit_req zone=classroom_server_limit burst=240 nodelay;"
    header = '        add_header Retry-After $nova_edge_retry_after always;'
    if marker in source:
        require(source.count(marker) == 1 and source.count(new_zone) == 1
                and source.count(global_zone) == 1 and source.count(new_limit) == 2
                and source.count(global_limit) == 2 and source.count(header) == 1
                and source.count("limit_req_status 429;") == 1
                and old_limit not in source and old_zone not in source,
                "PARTIAL_CLASSROOM_EDGE_POLICY_REQUIRES_REVIEW")
        return source
    anchor = '        add_header Referrer-Policy "strict-origin-when-cross-origin" always;'
    require(source.count(old_zone) == 1 and source.count(old_limit) == 2
            and source.count(anchor) == 1 and "classroom_server_limit" not in source
            and "nova_edge_retry_after" not in source and "limit_req_status" not in source,
            "UNRECOGNIZED_CLASSROOM_EDGE_BASELINE")
    policy = "\n".join(["    " + marker, new_zone, global_zone,
                          "    limit_req_status 429;",
                          "    map $limit_req_status $nova_edge_retry_after {",
                          '        default "";', '        REJECTED "1";', "    }"])
    return source.replace(old_zone, policy).replace(
        old_limit, new_limit + "\n            " + global_limit).replace(anchor, anchor + "\n" + header)


def classroom_proxy_config(source, project):
    """Bound TEST NAT connections as well as request rate; leave PROD unchanged."""
    source = _classroom_rate_policy(source, project)
    if project != "nova-test":
        return source
    marker = "# nova-test classroom connection policy v1"
    old_limit = "        limit_conn conn_limit 100;"
    new_limit = "        limit_conn conn_limit 256;"
    total_zone = "    limit_conn_zone $server_name zone=classroom_conn_total:10m;"
    total_limit = "        limit_conn classroom_conn_total 320;"
    status = "        limit_conn_status 429;"
    old_map = '\n'.join(["    map $limit_req_status $nova_edge_retry_after {",
                         '        default "";', '        REJECTED "1";', "    }"])
    new_map = '\n'.join(['    map "$limit_req_status:$limit_conn_status" $nova_edge_retry_after {',
                         '        default "";', '        ~(^|:)REJECTED(:|$) "1";', "    }"])
    if marker in source:
        require(source.count(marker) == 1 and source.count(new_limit) == 1
                and source.count(total_zone) == 1 and source.count(total_limit) == 1
                and source.count(status) == 1 and source.count(new_map) == 1
                and old_limit not in source and old_map not in source,
                "PARTIAL_CLASSROOM_CONNECTION_POLICY_REQUIRES_REVIEW")
        return source
    zone_anchor = "    limit_conn_zone $binary_remote_addr zone=conn_limit:10m;"
    require(source.count(old_limit) == 1 and source.count(zone_anchor) == 1
            and source.count(old_map) == 1 and "classroom_conn_total" not in source
            and "limit_conn_status" not in source,
            "UNRECOGNIZED_CLASSROOM_CONNECTION_BASELINE")
    return source.replace(zone_anchor, zone_anchor + "\n    " + marker + "\n" + total_zone).replace(
        old_limit, "\n".join([new_limit, total_limit, status])).replace(old_map, new_map)


def plan(project, deploy_dir, image, web, edge, network, nginx, host_config, extra_containers=None,
         worker_instances=None):
    require(project in PROJECTS, "PROJECT_OUT_OF_SCOPE")
    require(re.fullmatch(r"ghcr\.io/[A-Za-z0-9_./-]+@sha256:[0-9a-f]{64}", image), "IMMUTABLE_IMAGE_REQUIRED")
    workspace = os.environ.get("GITHUB_WORKSPACE", "").strip()
    if workspace:
        require(not deploy_dir.resolve().is_relative_to(Path(workspace).resolve()),
                "RELEASE_INSIDE_ACTIONS_CHECKOUT")
    require(sha(host_config) == HOST_SHA, "HOST_PROXY_CHANGED_REAUDIT_REQUIRED")
    require(re.search(r"(?:127\.0\.0\.1|localhost):" + str(PROJECTS[project]) + r"(?![0-9])",
                      host_config.decode()), "HOST_LOOPBACK_UPSTREAM_MISSING")
    network_name = project + "_default"
    for container in (web, edge):
        require(container["State"]["Running"] and
                container["Config"]["Labels"].get("com.docker.compose.project") == project
                and set(container["NetworkSettings"]["Networks"]) == {network_name},
                "LIVE_PROJECT_OR_NETWORK_CHANGED")
    edge_network = edge["NetworkSettings"]["Networks"][network_name]
    address = ipaddress.ip_address(edge_network["IPAddress"])
    gateway = ipaddress.ip_address(edge_network["Gateway"])
    ipv4 = [item for item in network["IPAM"]["Config"] if ":" not in item.get("Subnet", "")]
    require(len(ipv4) == 1 and address.version == gateway.version == 4
            and str(gateway) == ipv4[0].get("Gateway")
            and address in ipaddress.ip_network(ipv4[0]["Subnet"])
            and address != gateway, "NETWORK_ADDRESSES_REQUIRE_REVIEW")
    mounts = [item for item in web["Mounts"] if item["Destination"] == "/app/.data"]
    require(len(mounts) == 1 and mounts[0].get("Name") == project + "_nova-data"
            and mounts[0]["RW"], "WRONG_BUSINESS_VOLUME")
    env = dict(item.split("=", 1) for item in web["Config"].get("Env") or [])
    limits = {}
    for key, default in LIMIT_DEFAULTS.items():
        # The independent IP window inherits the existing account window when
        # upgrading a pre-fix environment that has no explicit IP window yet.
        raw = env.get(key, env.get("NLP_AGENT_AUTH_RATE_WINDOW_S", str(default))
                      if key.endswith("IP_RATE_WINDOW_S") else str(default))
        require(raw.isdigit() and 1 <= int(raw) <= 86400, "LOGIN_LIMIT_REQUIRES_REVIEW")
        limits[key] = raw
    limits["FORWARDED_ALLOW_IPS"] = "127.0.0.1,::1," + str(address)
    config = classroom_proxy_config(proxy_config(nginx), project)
    require("set_real_ip_from" not in config, "INLINE_PROXY_TRUST_REQUIRES_REVIEW")
    configs = {"nginx.conf": config.encode(),
               "trusted-edge-http.conf": f"set_real_ip_from 127.0.0.1;\nset_real_ip_from ::1;\nset_real_ip_from {gateway};\n".encode(),
               "trusted-edge-addresses.conf": f"127.0.0.1 1;\n::1 1;\n{gateway} 1;\n".encode()}
    fixed_ips = {"nginx": str(address), "nova-web": web["NetworkSettings"]["Networks"][network_name]["IPAddress"]}
    require(ipaddress.ip_address(fixed_ips["nova-web"]) in ipaddress.ip_network(ipv4[0]["Subnet"])
            and fixed_ips["nova-web"] not in (str(address), str(gateway)), "WEB_IP_REQUIRES_REVIEW")
    seen_addresses = {str(address), str(gateway), fixed_ips["nova-web"]}
    for service, container in (extra_containers or {}).items():
        require(service in ("nova-worker", "nova-monitor", "nova-sandbox-manager", "mysql", "redis")
                and container["State"]["Running"]
                and container["Config"]["Labels"].get("com.docker.compose.project") == project
                and set(container["NetworkSettings"]["Networks"]) == {network_name},
                "OTHER_PROJECT_SERVICE_REQUIRES_REVIEW")
        fixed = container["NetworkSettings"]["Networks"][network_name]["IPAddress"]
        require(ipaddress.ip_address(fixed) in ipaddress.ip_network(ipv4[0]["Subnet"])
                and fixed not in seen_addresses, "SERVICE_IP_REQUIRES_REVIEW")
        seen_addresses.add(fixed)
        if not (project == "nova-test" and service == "nova-worker"):
            fixed_ips[service] = fixed
    metadata = {"schema": 1, "project": project, "image": image, "volume": mounts[0]["Name"],
                "web_id": web["Id"], "nginx_id": edge["Id"],
                "nginx_ip": str(address), "gateway": str(gateway), "limits": limits,
                "fixed_ips": fixed_ips,
                "config_hashes": {key: sha(value) for key, value in configs.items()}}
    if project == "nova-test":
        metadata["schema"] = 2
        metadata["worker_subnet"] = ipv4[0]["Subnet"]
        metadata["worker_ids"] = worker_snapshot(
            project, worker_instances or [], ipaddress.ip_network(ipv4[0]["Subnet"]),
            [*fixed_ips.values(), str(gateway)])
        require(extra_containers and extra_containers.get("nova-worker", {}).get("Id") in metadata["worker_ids"],
                "WORKER_SNAPSHOT_CHANGED_DURING_PREPARATION")
    release = deploy_dir / "auth-releases" / release_identity(metadata)
    guard(release)
    override = {"services": {
        "nginx": {"volumes": [{"type": "bind", "source": str(release / name),
                                   "target": "/etc/nginx/" + name, "read_only": True} for name in configs],
                  "networks": {"default": {"ipv4_address": str(address)}}},
        "nova-web": {"environment": limits}},
        "networks": {"default": {"name": network_name, "external": True}}}
    for service, fixed in fixed_ips.items():
        override["services"].setdefault(service, {})["networks"] = {"default": {"ipv4_address": fixed}}
    if "worker_ids" in metadata:
        override["services"]["nova-worker"] = {
            "networks": {"default": {}}, "deploy": {"replicas": len(metadata["worker_ids"])}}
    metadata["release_dir"] = str(release)
    return override, metadata, configs


def prepare(args):
    project, deploy_dir, image = args.project, Path(args.deploy_dir), args.image
    guard(deploy_dir)
    require(deploy_dir.is_dir(), "DEPLOY_DIRECTORY_MISSING")
    web, edge = inspect(project + "-nova-web-1"), inspect(project + "-nginx-1")
    nginx = run(["docker", "exec", project + "-nginx-1", "cat", "/etc/nginx/nginx.conf"]).decode()
    worker_instances = workers(project) if project == "nova-test" else None
    extra = {service: inspect(project + "-" + service + "-1") for service in
             ("nova-monitor", "nova-sandbox-manager", "mysql", "redis")}
    extra["nova-worker"] = worker_instances[0] if worker_instances else inspect(project + "-nova-worker-1")
    override, metadata, configs = plan(project, deploy_dir, image, web, edge,
                                      inspect(project + "_default"), nginx, HOST_CONFIG.read_bytes(), extra,
                                      worker_instances)
    release = Path(metadata["release_dir"])
    if args.mode == "preview":
        print(json.dumps({"status": "READ_ONLY_AUTH_RELEASE_PREVIEW", **metadata,
                          "live_configuration_changed": False}))
        return
    release.mkdir(parents=True, mode=0o700, exist_ok=True)
    for name, data in configs.items():
        immutable(release / name, data)
    immutable(release / "prepared.json", json.dumps(metadata, sort_keys=True, indent=2).encode())
    target = release / "compose.auth.json"
    if target.exists():
        saved = json.loads(target.read_bytes())
        comparable = json.loads(json.dumps(saved))
        comparable["services"]["nova-web"]["environment"].pop("NLP_AGENT_WEB_STATIC_DIR", None)
        require(comparable == override, "EXISTING_COMPOSE_OVERLAY_CHANGED")
    else:
        immutable(target, json.dumps(override, indent=2).encode())
    print(str(target))


def read_prepared(args):
    path, deploy_dir = Path(args.override), Path(args.deploy_dir)
    guard(path)
    guard(deploy_dir)
    require(path.is_relative_to(deploy_dir / "auth-releases") and path.name == "compose.auth.json",
            "OVERLAY_OUTSIDE_DEPLOY_DIRECTORY")
    metadata = json.loads((path.parent / "prepared.json").read_bytes())
    require(metadata["project"] == args.project and metadata["image"] == args.image
            and metadata["release_dir"] == str(path.parent), "WRONG_PREPARED_RELEASE")
    require(metadata["volume"] == args.project + "_nova-data"
            and set(metadata["config_hashes"]) == {"nginx.conf", "trusted-edge-http.conf", "trusted-edge-addresses.conf"},
            "PREPARED_RESOURCE_SCOPE_CHANGED")
    require(path.parent.name == release_identity(metadata), "PREPARED_METADATA_CHANGED")
    for name, checksum in metadata["config_hashes"].items():
        require(sha((path.parent / name).read_bytes()) == checksum, "PREPARED_PROXY_FILE_CHANGED")
    return path, metadata


def stage(args):
    path, metadata = read_prepared(args)
    web = inspect(args.project + "-nova-web-1")
    require(web["Id"] == metadata["web_id"] and inspect(args.project + "-nginx-1")["Id"] == metadata["nginx_id"],
            "LIVE_CONTAINERS_CHANGED_DURING_PREPARATION")
    if "worker_ids" in metadata:
        current = worker_snapshot(args.project, workers(args.project),
                                  ipaddress.ip_network(metadata["worker_subnet"]),
                                  [*metadata["fixed_ips"].values(), metadata["gateway"]])
        require(current == metadata["worker_ids"],
                "WORKER_INSTANCES_CHANGED_DURING_PREPARATION")
    raw = run(["docker", "exec", args.project + "-nova-web-1", ".venv/bin/python", "-c",
               "import json; from configs.settings import Settings; print(json.dumps(Settings().web_runtime['static_dir']))"])
    previous = Path(json.loads(raw))
    extra_mount = []
    if previous in (Path("webui/dist"), Path("/app/webui/dist")):
        copied = Path(tempfile.mkdtemp(prefix="previous-baked-", dir=path.parent))
        run(["docker", "cp", args.project + "-nova-web-1:/app/webui/dist/.", str(copied)])
        previous = Path("/previous-frontend")
        extra_mount = ["--mount", f"type=bind,source={copied},target=/previous-frontend,readonly"]
    require(previous.is_absolute() and (previous.is_relative_to(Path("/app/.data/developer"))
                                       or previous == Path("/previous-frontend")), "UNEXPECTED_OLD_FRONTEND_PATH")
    output = run(["docker", "run", "--rm", "--pull=never", "--network", "none", "--read-only",
                  "--memory=384m", "--cpus=0.5", "--tmpfs", "/tmp:rw,size=32m",
                  "--mount", f"type=volume,source={metadata['volume']},target=/app/.data", *extra_mount,
                  "--entrypoint", "/app/.venv/bin/python", args.image,
                  "-B", "/app/scripts/stage_release_frontend.py", "--previous", str(previous)])
    result = json.loads(output)
    root = Path("/app/.data/developer/ci-release-frontends")
    require(result["status"] in ("RELEASE_FRONTEND_STAGED", "RELEASE_FRONTEND_ALREADY_STAGED")
            and re.fullmatch(r"[0-9a-f]{64}", result["index_sha256"])
            and Path(result["static_dir"]) == root / result["index_sha256"] / "dist", "FRONTEND_STAGE_RESULT_INVALID")
    override = json.loads(path.read_bytes())
    env = override["services"]["nova-web"]["environment"]
    require({key: env.get(key) for key in metadata["limits"]} == metadata["limits"], "PREPARED_LIMITS_CHANGED")
    env["NLP_AGENT_WEB_STATIC_DIR"] = result["static_dir"]
    fd, temporary = tempfile.mkstemp(prefix=".compose-", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(override, file, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)
    immutable(path.parent / "frontend.json", json.dumps(
        {key: result[key] for key in ("static_dir", "index_sha256")}, sort_keys=True, indent=2).encode())
    print(json.dumps({"status": "AUTH_RELEASE_FRONTEND_PREPARED", "index_sha256": result["index_sha256"],
                      "runtime_configuration_changed": False}))


def check_compose(config, metadata, frontend):
    services = config["services"]
    environment = services["nova-web"]["environment"]
    for name, expected in metadata["limits"].items():
        require(str(environment.get(name)) == expected, "RESOLVED_AUTH_ENVIRONMENT_MISMATCH")
    require(environment.get("NLP_AGENT_WEB_STATIC_DIR") == frontend["static_dir"], "RESOLVED_FRONTEND_PATH_MISMATCH")
    edge = services["nginx"]
    require(edge["networks"]["default"]["ipv4_address"] == metadata["nginx_ip"], "RESOLVED_PROXY_IP_MISMATCH")
    for service, address in metadata["fixed_ips"].items():
        require(services[service]["networks"]["default"]["ipv4_address"] == address,
                "RESOLVED_SERVICE_IP_MISMATCH")
    if "worker_ids" in metadata:
        worker = services["nova-worker"]
        require(metadata["project"] == "nova-test" and 1 <= len(metadata["worker_ids"]) <= 2
                and set(worker.get("networks", {})) == {"default"}
                and not worker["networks"]["default"]
                and worker.get("deploy", {}).get("replicas") == len(metadata["worker_ids"])
                and not worker.get("container_name") and not worker.get("ports"),
                "RESOLVED_WORKER_SCALING_CONFIGURATION_MISMATCH")
    network = config["networks"]["default"]
    require(network["name"] == metadata["project"] + "_default" and network.get("external") is True,
            "RESOLVED_NETWORK_RECREATE_REFUSED")
    ports = edge["ports"]
    require(len(ports) == 1 and ports[0].get("host_ip") == "127.0.0.1"
            and int(ports[0]["published"]) == PROJECTS[metadata["project"]]
            and int(ports[0]["target"]) == 80, "RESOLVED_EDGE_NOT_LOOPBACK")
    require(not services["nova-web"].get("ports") and not services["mysql"].get("ports")
            and not services["redis"].get("ports"), "UNEXPECTED_PUBLIC_BACKEND_PORTS")
    volumes = config["volumes"]
    for name in ("mysql-data", "redis-data", "nova-data", "nova-logs"):
        require(volumes[name]["name"] == metadata["project"] + "_" + name,
                "RESOLVED_BUSINESS_VOLUME_CROSSES_PROJECT")
    for service in services.values():
        for mount in service.get("volumes", []):
            if mount.get("type") == "volume":
                logical = mount.get("source")
                require(logical in volumes and volumes[logical]["name"].startswith(metadata["project"] + "_"),
                        "RESOLVED_FOREIGN_DATA_VOLUME")
    for name in ("nova-web", "nova-worker", "nova-migrate", "nova-monitor", "nova-sandbox-manager"):
        require(services[name]["image"] == metadata["image"], "RESOLVED_APPLICATION_IMAGE_MISMATCH")
    mounts = {item["target"]: item for item in edge["volumes"]}
    for name in metadata["config_hashes"]:
        item = mounts.get("/etc/nginx/" + name, {})
        require(item.get("type") == "bind" and item.get("read_only") is True
                and item.get("source") == str(Path(metadata["release_dir"]) / name),
                "RESOLVED_SHARED_OR_WRITABLE_PROXY_CONFIGURATION")


def verify(args):
    path, metadata = read_prepared(args)
    frontend = json.loads((path.parent / "frontend.json").read_bytes())
    if args.mode == "check":
        require(args.compose_json, "RESOLVED_COMPOSE_JSON_REQUIRED")
        check_compose(json.loads(Path(args.compose_json).read_bytes()), metadata, frontend)
        print(json.dumps({"status": "AUTH_RELEASE_RESOLVED_PLAN_PASSED"}))
        return
    web, edge = inspect(args.project + "-nova-web-1"), inspect(args.project + "-nginx-1")
    require(web["State"]["Running"] and edge["State"]["Running"], "DEPLOYED_SERVICES_NOT_RUNNING")
    require(web["Image"] == inspect(args.image)["Id"], "DEPLOYED_IMAGE_MISMATCH")
    if "worker_ids" in metadata:
        deployed_workers = workers(args.project)
        worker_snapshot(args.project, deployed_workers, ipaddress.ip_network(metadata["worker_subnet"]),
                        [*metadata["fixed_ips"].values(), metadata["gateway"]])
        require(len(deployed_workers) == len(metadata["worker_ids"])
                and all(item["State"]["Running"] and item["Image"] == web["Image"]
                        and set(item["NetworkSettings"]["Networks"]) == {args.project + "_default"}
                        and not item["HostConfig"].get("PortBindings") for item in deployed_workers),
                "DEPLOYED_WORKER_REPLICAS_MISMATCH")
    env = dict(item.split("=", 1) for item in web["Config"]["Env"])
    require(all(env.get(key) == value for key, value in metadata["limits"].items())
            and env.get("NLP_AGENT_WEB_STATIC_DIR") == frontend["static_dir"], "DEPLOYED_AUTH_SETTINGS_MISMATCH")
    network = edge["NetworkSettings"]["Networks"][args.project + "_default"]
    require(network["IPAddress"] == metadata["nginx_ip"], "DEPLOYED_PROXY_IP_MISMATCH")
    ports = edge["NetworkSettings"]["Ports"].get("80/tcp")
    require(ports == [{"HostIp": "127.0.0.1", "HostPort": str(PROJECTS[args.project])}],
            "DEPLOYED_PROXY_NOT_LOOPBACK")
    for name, checksum in metadata["config_hashes"].items():
        require(sha(run(["docker", "exec", args.project + "-nginx-1", "cat", "/etc/nginx/" + name])) == checksum,
                "DEPLOYED_PROXY_CONFIGURATION_MISMATCH")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{PROJECTS[args.project]}/", timeout=8) as response:
        require(response.status == 200 and sha(response.read()) == frontend["index_sha256"],
                "DEPLOYED_FRONTEND_HASH_MISMATCH")
    print(json.dumps({"status": "AUTH_RELEASE_LIVE_CONFIGURATION_PASSED", "project": args.project,
                      "index_sha256": frontend["index_sha256"]}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preview", "prepare", "stage", "check", "verify"))
    parser.add_argument("--project", required=True, choices=tuple(PROJECTS))
    parser.add_argument("--deploy-dir", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--override")
    parser.add_argument("--compose-json")
    args = parser.parse_args()
    os.umask(0o077)
    if args.mode in ("stage", "check", "verify"):
        require(args.override, "OVERLAY_REQUIRED")
        if args.mode == "stage":
            stage(args)
        else:
            verify(args)
    else:
        prepare(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Docker inspection may contain credentials. Never print raw output.
        print(json.dumps({"status": "AUTH_RELEASE_PREPARATION_STOPPED",
                          "error_code": str(error) if isinstance(error, ValueError) else type(error).__name__}),
              file=__import__("sys").stderr)
        raise SystemExit(1)
