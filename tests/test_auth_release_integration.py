"""Check deployment boundaries with synthetic metadata and temporary files."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from configs.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.fixture
def release():
    return module("auth_release", ROOT / ".github/scripts/prepare-auth-release.py")


@pytest.fixture
def stager():
    return module("frontend_stage", ROOT / "scripts/stage_release_frontend.py")


@pytest.fixture
def planned(release, tmp_path, monkeypatch):
    project = "nova-test"
    net_name = project + "_default"
    host = b"proxy_pass http://127.0.0.1:18765;"
    monkeypatch.setattr(release, "HOST_SHA", release.sha(host))
    def container(identifier, address):
        return {"Id": identifier, "State": {"Running": True},
                "Config": {"Labels": {"com.docker.compose.project": project},
                           "Env": ["NLP_AGENT_AUTH_RATE_WINDOW_S=900", "DEEPSEEK_API_KEY=never-copy-this"]},
                "Mounts": [{"Destination": "/app/.data", "Name": project + "_nova-data", "RW": True}],
                "NetworkSettings": {"Networks": {net_name: {"IPAddress": address, "Gateway": "172.18.0.1"}}}}
    web, edge = container("web-before", "172.18.0.4"), container("nginx-before", "172.18.0.8")
    net = {"IPAM": {"Config": [{"Subnet": "172.18.0.0/16", "Gateway": "172.18.0.1"}]}}
    image = "ghcr.io/example/nlp-agent@sha256:" + "a" * 64
    nginx = (ROOT / "nginx/nginx.conf").read_text(encoding="utf-8")
    args = [project, tmp_path, image, web, edge, net, nginx, host]
    overlay, metadata, configs = release.plan(*args)
    return SimpleNamespace(args=args, overlay=overlay, metadata=metadata, configs=configs)


def resolved(planned):
    meta, overlay = planned.metadata, copy.deepcopy(planned.overlay)
    services = overlay["services"]
    frontend = {"static_dir": "/app/.data/developer/ci-release-frontends/" + "b" * 64 + "/dist"}
    services["nova-web"]["environment"]["NLP_AGENT_WEB_STATIC_DIR"] = frontend["static_dir"]
    services["nginx"]["ports"] = [{"host_ip": "127.0.0.1", "published": "18765", "target": 80}]
    for name in ("nova-web", "nova-worker", "nova-migrate", "nova-monitor", "nova-sandbox-manager"):
        services.setdefault(name, {})["image"] = meta["image"]
    services["mysql"] = {}
    services["redis"] = {}
    overlay["volumes"] = {name: {"name": meta["project"] + "_" + name}
                          for name in ("mysql-data", "redis-data", "nova-data", "nova-logs")}
    return overlay, frontend


def test_proxy_plan_uses_exact_peer_gateway_and_immutable_files(release, planned):
    metadata, overlay = planned.metadata, planned.overlay
    assert metadata["limits"]["FORWARDED_ALLOW_IPS"] == "127.0.0.1,::1,172.18.0.8"
    assert metadata["limits"]["NLP_AGENT_AUTH_MAX_LOGIN_ATTEMPTS"] == "5"
    assert metadata["limits"]["NLP_AGENT_AUTH_MAX_IP_LOGIN_ATTEMPTS"] == "100"
    assert metadata["limits"]["NLP_AGENT_AUTH_IP_RATE_WINDOW_S"] == "900"
    assert "172.18.0.1;" in planned.configs["trusted-edge-http.conf"].decode()
    assert "/16" not in planned.configs["trusted-edge-http.conf"].decode()
    assert overlay["networks"]["default"] == {"name": "nova-test_default", "external": True}
    assert "never-copy-this" not in json.dumps(metadata)
    assert all(item["read_only"] for item in overlay["services"]["nginx"]["volumes"])


def test_release_files_survive_actions_checkout_cleanup(release, planned, monkeypatch):
    deploy_dir = planned.args[1]
    monkeypatch.setenv("GITHUB_WORKSPACE", str(deploy_dir.parent))
    with pytest.raises(ValueError, match="RELEASE_INSIDE_ACTIONS_CHECKOUT"):
        release.plan(*planned.args)
    monkeypatch.setenv("GITHUB_WORKSPACE", str(deploy_dir / "checkout"))
    release.plan(*planned.args)


@pytest.mark.parametrize("change,code", [("host", "HOST_PROXY_CHANGED"), ("volume", "WRONG_BUSINESS_VOLUME"),
                                        ("network", "LIVE_PROJECT_OR_NETWORK"), ("image", "IMMUTABLE_IMAGE")])
def test_plan_rejects_changed_host_or_foreign_resources(release, planned, change, code):
    args = copy.deepcopy(planned.args)
    if change == "host":
        args[7] += b"changed"
    elif change == "volume":
        args[3]["Mounts"][0]["Name"] = "nova-prod_nova-data"
    elif change == "network":
        args[4]["Config"]["Labels"]["com.docker.compose.project"] = "nova-prod"
    else:
        args[2] = "ghcr.io/example/nlp-agent:latest"
    with pytest.raises(ValueError, match=code):
        release.plan(*args)


def test_bounded_nginx_upgrade_preserves_other_routes(release):
    old = ('http {\n    log_format main x;\n'
           'proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n'
           'proxy_set_header X-Forwarded-Proto $scheme;\nlocation /kept { return 200; }\n}')
    result = release.proxy_config(old)
    assert "location /kept { return 200; }" in result
    assert "$proxy_add_x_forwarded_for" not in result
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in result
    assert release.proxy_config(result) == result
    with pytest.raises(ValueError, match="UNRECOGNIZED"):
        release.proxy_config("unrecognized layout")


def test_compose_plan_validates_frontend_limits_and_volume_isolation(release, planned):
    config, frontend = resolved(planned)
    release.check_compose(config, planned.metadata, frontend)
    config["volumes"]["nova-data"]["name"] = "nova-prod_nova-data"
    with pytest.raises(ValueError, match="BUSINESS_VOLUME_CROSSES_PROJECT"):
        release.check_compose(config, planned.metadata, frontend)


@pytest.mark.parametrize("change", ["public", "trust", "frontend", "image"])
def test_resolved_compose_cannot_broaden_or_drop_the_fix(release, planned, change):
    config, frontend = resolved(planned)
    if change == "public":
        config["services"]["nginx"]["ports"][0]["host_ip"] = "0.0.0.0"
    elif change == "trust":
        config["services"]["nova-web"]["environment"]["FORWARDED_ALLOW_IPS"] = "*"
    elif change == "frontend":
        config["services"]["nova-web"]["environment"].pop("NLP_AGENT_WEB_STATIC_DIR")
    else:
        config["services"]["nova-worker"]["image"] = "other:latest"
    with pytest.raises(ValueError):
        release.check_compose(config, planned.metadata, frontend)


def test_release_metadata_and_proxy_files_are_immutable(release, planned):
    parent = Path(planned.metadata["release_dir"])
    parent.mkdir(parents=True)
    for name, content in planned.configs.items():
        release.immutable(parent / name, content)
    release.immutable(parent / "prepared.json", json.dumps(planned.metadata).encode())
    path = parent / "compose.auth.json"
    path.write_text(json.dumps(planned.overlay))
    args = SimpleNamespace(override=str(path), deploy_dir=planned.args[1],
                           project="nova-test", image=planned.args[2])
    assert release.read_prepared(args)[1]["volume"] == "nova-test_nova-data"
    with pytest.raises(ValueError, match="EXISTING_RELEASE_FILE_CHANGED"):
        release.immutable(parent / "nginx.conf", b"different")
    altered = copy.deepcopy(planned.metadata)
    altered["volume"] = "nova-prod_nova-data"
    (parent / "prepared.json").write_text(json.dumps(altered))
    with pytest.raises(ValueError, match="RESOURCE_SCOPE_CHANGED"):
        release.read_prepared(args)


def frontend_fixture(tmp_path):
    new, old, root = tmp_path / "new", tmp_path / "old", tmp_path / "staged"
    for path in (new / "assets", old / "assets"):
        path.mkdir(parents=True)
    (new / "index.html").write_text('<script src="/assets/new.js"></script>')
    (new / "assets/new.js").write_text("new")
    (old / "index.html").write_text("old-index")
    (old / "assets/old-tab.js").write_text("old-tab")
    return new, old, root


def test_frontend_stage_keeps_old_tabs_and_does_not_edit_runtime_yaml(stager, tmp_path):
    new, old, root = frontend_fixture(tmp_path)
    runtime = tmp_path / "runtime-overrides.yaml"
    runtime.write_text("web:\n  static_dir: old\ngateway:\n  quota_enforcement: true\n")
    before = runtime.read_bytes()
    result = stager.stage_frontend(new, old, root)
    target = Path(result["static_dir"])
    assert (target / "assets/old-tab.js").read_text() == "old-tab"
    assert (target / "assets/new.js").read_text() == "new"
    assert runtime.read_bytes() == before
    assert (old / "index.html").read_text() == "old-index"
    assert stager.stage_frontend(new, old, root)["status"] == "RELEASE_FRONTEND_ALREADY_STAGED"
    (target / "assets/new.js").write_text("changed")
    with pytest.raises(ValueError, match="EXISTING_FRONTEND_CHANGED"):
        stager.stage_frontend(new, old, root)


def test_missing_frontend_entry_asset_stops_before_staging(stager, tmp_path):
    new, old, root = frontend_fixture(tmp_path)
    (new / "assets/new.js").unlink()
    with pytest.raises(ValueError, match="ENTRY_ASSET_MISSING"):
        stager.stage_frontend(new, old, root)
    assert not root.exists() and (old / "index.html").read_text() == "old-index"


def test_frontend_copy_failure_never_changes_old_or_publishes_partial(stager, tmp_path, monkeypatch):
    new, old, root = frontend_fixture(tmp_path)
    def fail(*args):
        raise OSError("injected_copy_failure")
    monkeypatch.setattr(stager.shutil, "copyfile", fail)
    with pytest.raises(OSError, match="injected_copy_failure"):
        stager.stage_frontend(new, old, root)
    assert (old / "index.html").read_text() == "old-index"
    assert not (root / stager.checksum(new / "index.html")).exists()


def test_explicit_release_frontend_setting_preserves_runtime_config():
    settings = Settings(_env_file=None, NLP_AGENT_WEB_STATIC_DIR="/release/dist")
    settings._config["web"] = {"static_dir": "/old/dist", "host": "127.0.0.1"}
    assert settings.web_runtime["static_dir"] == "/release/dist"
    assert settings._config["web"]["static_dir"] == "/old/dist"
    settings.NLP_AGENT_WEB_STATIC_DIR = ""
    assert settings.web_runtime["static_dir"] == "/old/dist"
