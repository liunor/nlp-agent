"""Opt-in Linux/gVisor integration checks; skipped on developer machines."""
from __future__ import annotations

import json
import os
import subprocess
import uuid

import pytest

pytestmark = pytest.mark.skipif(os.getenv("RUN_SANDBOX_DOCKER_INTEGRATION") != "1", reason="Linux gVisor CI only")
DOCKER_COMMAND_TIMEOUT_SECONDS = 60


def _docker_run(command: list[str], **kwargs):
    kwargs.setdefault("timeout", DOCKER_COMMAND_TIMEOUT_SECONDS)
    return subprocess.run(command, **kwargs)


def test_runsc_container_denies_network_and_docker_socket() -> None:
    image = os.environ["NLP_AGENT_SANDBOX_INTEGRATION_IMAGE"]
    name = f"nova-security-{uuid.uuid4().hex}"
    command = ["docker", "run", "--detach", "--name", name, "--runtime", "runsc", "--read-only", "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true", "--memory", "768m", "--cpus", "1", "--pids-limit", "128", "--tmpfs", "/workspace:rw,nosuid,nodev,size=256m", "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m", "--tmpfs", "/run/nova:rw,nosuid,nodev,uid=10001,gid=10001,mode=700,size=16m", "--user", "10001:10001", image]
    try:
        _docker_run(command, check=True, capture_output=True, text=True)
        inspect = _docker_run(
            ["docker", "inspect", name, "--format", "{{json .HostConfig}}"],
            check=True, capture_output=True, text=True,
        ).stdout
        assert '"ReadonlyRootfs":true' in inspect
        assert '"NetworkMode":"none"' in inspect
        assert '"PidsLimit":128' in inspect
        assert '"Memory":805306368' in inspect
        assert '"CapDrop":["ALL"]' in inspect
        identity = _docker_run(["docker", "exec", name, "id", "-u"], check=True, capture_output=True, text=True)
        assert identity.stdout.strip() == "10001"
        network = _docker_run(["docker", "exec", name, "python", "-c", "import socket; socket.create_connection(('1.1.1.1', 53), timeout=1)"], capture_output=True, text=True)
        socket_check = _docker_run(["docker", "exec", name, "python", "-c", "import os; raise SystemExit(os.path.exists('/var/run/docker.sock'))"], capture_output=True, text=True)
        rootfs_check = _docker_run(["docker", "exec", name, "python", "-c", "open('/etc/nova-write-check', 'w').write('x')"], capture_output=True, text=True)
        symlink_check = _docker_run(["docker", "exec", name, "python", "-c", "import os; os.symlink('/etc/passwd', '/workspace/escape'); open('/workspace/escape', 'w').write('x')"], capture_output=True, text=True)
        privilege_check = _docker_run(["docker", "exec", name, "python", "-c", "import os; os.setuid(0)"], capture_output=True, text=True)
        flood = _docker_run(
            ["docker", "exec", "--interactive", name, "python", "/opt/nova-runtime/nova_runtime.py", "scratch", "--timeout-seconds", "10", "--output-limit-bytes", "1024"],
            input=json.dumps({"source": "print('x' * 200000)"}), capture_output=True, text=True,
        )
        assert network.returncode != 0
        assert socket_check.returncode == 0
        assert rootfs_check.returncode != 0
        assert symlink_check.returncode != 0
        assert privilege_check.returncode != 0
        assert flood.returncode == 0
        payload = json.loads(flood.stdout)
        assert len(payload.get("stdout", "").encode()) <= 1024
        assert payload.get("truncated") is True

        fork_probe = _docker_run(
            ["docker", "exec", name, "python", "-c", (
                # Keep this below the configured PID cap. The HostConfig
                # assertion above verifies the cap itself; this probe only
                # confirms ordinary fork/wait remains available under runsc.
                "import os; count=0; "
                "\nfor _ in range(64):\n"
                " try: child=os.fork()\n"
                " except OSError: break\n"
                " if child == 0: os._exit(0)\n"
                " os.waitpid(child, 0); count += 1\n"
                "print(count)"
            )],
            capture_output=True, text=True,
        )
        assert fork_probe.returncode == 0
        assert int(fork_probe.stdout.strip()) == 64

        memory_probe = _docker_run(
            ["docker", "exec", name, "python", "-c", "bytearray(2 * 1024 * 1024 * 1024)"],
            capture_output=True, text=True,
        )
        assert memory_probe.returncode != 0
    finally:
        _docker_run(["docker", "rm", "--force", name], capture_output=True, text=True)
