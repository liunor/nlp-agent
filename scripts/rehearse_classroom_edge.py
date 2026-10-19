"""Exercise actual nginx rate limiting on disposable, networkless containers."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
LABEL = "nlp-agent.classroom-edge-probe"
PROBE = r'''
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json,sys,threading,urllib.request,urllib.error
mode=sys.argv[1]
barrier=threading.Barrier(41) if mode=='classroom' else None
def request(index):
 headers={}
 if mode=='global-flood': headers['X-Forwarded-For']='203.0.113.'+str(index%200+1)
 req=urllib.request.Request('http://127.0.0.1/api/v1/sessions?limit=10',headers=headers)
 try:
  with urllib.request.urlopen(req,timeout=5) as response: return response.status,response.headers.get('Retry-After')
 except urllib.error.HTTPError as error: return error.code,error.headers.get('Retry-After')
def user(index):
 barrier.wait(timeout=20)
 return [request(index) for _ in range(3)]
with ThreadPoolExecutor(max_workers=41 if mode=='classroom' else 64) as pool:
 results=[r for group in pool.map(user,range(41)) for r in group] if mode=='classroom' else list(pool.map(request,range(600)))
print(json.dumps({'requests':len(results),'codes':dict(Counter(str(code) for code,_ in results)),
 'all_rejections_have_retry_after':all(header=='1' for code,header in results if code==429),
 'successes_have_no_retry_after':all(header is None for code,header in results if code==200)}))
'''


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError("ISOLATED_DOCKER_COMMAND_FAILED")
    return result.stdout


def module():
    spec = importlib.util.spec_from_file_location("release", ROOT / ".github/scripts/prepare-auth-release.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def owned_remove(identifier, nonce):
    if not re.fullmatch(r"[a-f0-9]{64}", identifier):
        raise ValueError("EXACT_OWNED_CONTAINER_ID_REQUIRED")
    item = json.loads(command(["docker", "inspect", identifier]))[0]
    if item["Config"]["Labels"].get(LABEL) != nonce:
        raise ValueError("FOREIGN_RESOURCE_REJECTED")
    command(["docker", "rm", "-f", identifier])


def exercise(directory, config, scenario, nonce, *, connection_fixture=False):
    # Static content exercises nginx's access phase; `return 200` would bypass
    # limit_req and would make the supposed regression test meaningless.
    if not connection_fixture:
        config = config.replace("proxy_pass http://nova_web;", "root /fixture;\n            try_files /ok.txt =404;")
    (directory / "nginx.conf").write_text(config, encoding="utf-8")
    args = ["docker", "run", "-d", "--network", "none", "--label", LABEL+"="+nonce,
            "--add-host", "nova-web:127.0.0.1", "--add-host", "nova-monitor:127.0.0.1",
            "--mount", "type=bind,src="+str(directory)+",dst=/fixture,readonly",
            "--mount", "type=bind,src="+str(directory/"nginx.conf")+",dst=/etc/nginx/nginx.conf,readonly",
            "--mount", "type=bind,src="+str(directory/"trusted-edge-http.conf")+",dst=/etc/nginx/trusted-edge-http.conf,readonly",
            "--mount", "type=bind,src="+str(directory/"trusted-edge-addresses.conf")+",dst=/etc/nginx/trusted-edge-addresses.conf,readonly",
            "nginx:1.27-alpine", "nginx", "-g", "daemon off;"]
    identifier = command(args).strip()
    try:
        item = json.loads(command(["docker", "inspect", identifier]))[0]
        assert item["HostConfig"]["NetworkMode"] == "none" and not item["HostConfig"].get("PortBindings")
        command(["docker", "exec", identifier, "nginx", "-t"])
        output = command(["docker", "run", "--rm", "--label", LABEL+"="+nonce,
                          "--network", "container:"+identifier,
                          "--mount", "type=bind,src="+str(directory)+",dst=/fixture,readonly",
                          "python:3.12-alpine", "python", "/fixture/" + (
                              "connection-probe.py" if connection_fixture else "probe.py"), scenario])
        report = json.loads(output)
        log = subprocess.run(["docker", "logs", identifier], capture_output=True, text=True, timeout=20)
        report["server_wide_rejections_logged"] = len(re.findall('by zone "classroom_server_limit"', log.stdout+log.stderr))
        report["per_ip_connection_rejections_logged"] = len(re.findall('limiting connections by zone "conn_limit"', log.stdout+log.stderr))
        report["total_connection_rejections_logged"] = len(re.findall('limiting connections by zone "classroom_conn_total"', log.stdout+log.stderr))
        return report
    finally:
        owned_remove(identifier, nonce)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "posix":
        raise ValueError("LINUX_ISOLATED_RUNNER_REQUIRED")
    nonce = uuid.uuid4().hex
    helper = module()
    original = (ROOT / "nginx/nginx.conf").read_text(encoding="utf-8")
    fixed = helper.classroom_proxy_config(original, "nova-test")
    assert helper.classroom_proxy_config(original, "nova-prod") == original
    with tempfile.TemporaryDirectory(prefix="nova-edge-"+nonce+"-") as temporary:
        directory = Path(temporary)
        directory.chmod(0o755)
        for name in ("trusted-edge-http.conf", "trusted-edge-addresses.conf"):
            (directory/name).write_bytes((ROOT/"nginx"/name).read_bytes())
        (directory/"ok.txt").write_text("fixture\n")
        (directory/"probe.py").write_text(PROBE)
        (directory/"connection-probe.py").write_bytes((ROOT/"scripts/classroom_connection_probe.py").read_bytes())
        old = exercise(directory, original, "classroom", nonce)
        assert old["codes"].get("503", 0) > 0, "OLD_SHARED_IP_FAILURE_NOT_REPRODUCED"
        classroom = exercise(directory, fixed, "classroom", nonce)
        assert classroom["codes"] == {"200": 123}, "CLASSROOM_BURST_FAILED"
        assert classroom["successes_have_no_retry_after"]
        flood = exercise(directory, fixed, "global-flood", nonce)
        assert flood["codes"].get("429", 0) > 0 and set(flood["codes"]) == {"200", "429"}
        assert flood["all_rejections_have_retry_after"] and flood["server_wide_rejections_logged"] > 0
        previous = helper._classroom_rate_policy(original, "nova-test")
        old_tabs = exercise(directory, previous, "two-tabs", nonce, connection_fixture=True)
        assert old_tabs["codes"].get("503", 0) > 0 and old_tabs["per_ip_connection_rejections_logged"] > 0
        tabs = exercise(directory, fixed, "two-tabs", nonce, connection_fixture=True)
        assert tabs["held_websockets"] == 82 and tabs["codes"] == {"200": 41}
        assert tabs["successes_have_no_retry_after"]
        ip_bound = exercise(directory, fixed, "per-ip-bound", nonce, connection_fixture=True)
        total_bound = exercise(directory, fixed, "global-bound", nonce, connection_fixture=True)
        for item in (ip_bound, total_bound):
            assert item["codes"] == {"429": 1} and item["all_rejections_have_retry_after"]
        assert ip_bound["per_ip_connection_rejections_logged"] == 1
        assert total_bound["total_connection_rejections_logged"] == 1
    report = {"status": "ISOLATED_REAL_NGINX_CLASSROOM_EDGE_PASSED", "old": old, "classroom": classroom,
              "rotating_address_flood": flood, "previous_two_tabs": old_tabs, "fixed_two_tabs": tabs,
              "per_ip_connection_guard": ip_bound, "global_connection_guard": total_bound,
              "public_ports": False, "business_volumes_mounted": False,
              "live_services_changed": False, "production_configuration_unchanged": True,
              "model_calls": 0, "email_calls": 0, "only_owned_containers_removed": True,
              "scope_limit": "Networkless nginx static/proxy fixtures; not VPS application capacity or live deployment acceptance."}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
