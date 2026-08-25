from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch


def test_docker_runtime_command_has_no_host_or_network_escape_hatches() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeConfig, DockerRuntimeAdapter

    command = DockerRuntimeAdapter(DockerRuntimeConfig(image="registry.example/nova@sha256:" + "a" * 64)).create_command(
        name="nova-warm-python-base-1", claim_nonce="nonce"
    )

    assert command[:3] == ("docker", "run", "--detach")
    assert "--read-only" in command
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--pids-limit") + 1] == "128"
    assert all("docker.sock" not in item for item in command)
    assert "nonce" not in command
    assert "--volume" not in command
    assert "/run/nova:rw,nosuid,nodev,uid=10001,gid=10001,mode=700,size=16m" in command
    assert command[-1] == "registry.example/nova@sha256:" + "a" * 64
    assert "nova.sandbox.managed=true" in command


def test_docker_runtime_rejects_mutable_image_references() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeConfig

    try:
        DockerRuntimeConfig(image="nova-sandbox-runtime:local")
    except ValueError as error:
        assert "digest" in str(error)
    else:
        raise AssertionError("mutable Docker image reference must be rejected")


def test_l1_container_command_is_created_not_started() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig

    command = DockerRuntimeAdapter(DockerRuntimeConfig(image="registry.example/nova@sha256:" + "d" * 64)).create_l1_command(name="l1")

    assert command[:2] == ("docker", "create")
    assert "--detach" not in command


def test_production_runtime_uses_gvisor_runsc() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig

    command = DockerRuntimeAdapter(DockerRuntimeConfig(image="registry.example/nova@sha256:" + "e" * 64)).create_command(name="runsc", claim_nonce="")

    assert command[command.index("--runtime") + 1] == "runsc"


def test_kernel_readiness_requires_a_running_container_and_kernel_connection_file() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig

    adapter = DockerRuntimeAdapter(DockerRuntimeConfig(image="registry.example/nova@sha256:" + "b" * 64))

    async def exercise() -> bool:
        with patch.object(adapter, "healthy", new=AsyncMock(return_value=True)), patch(
            "server.sandbox.docker_runtime.asyncio.create_subprocess_exec"
        ) as spawn:
            process = AsyncMock()
            process.returncode = 0
            process.communicate.return_value = (b"", b"")
            spawn.return_value = process
            return await adapter.kernel_ready("container-id")

    assert asyncio.run(exercise())


def test_execution_protocol_uses_stdin_not_a_docker_command_argument() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig

    adapter = DockerRuntimeAdapter(DockerRuntimeConfig(image="registry.example/nova@sha256:" + "c" * 64))

    assert adapter.execute_command("container-id", timeout_seconds=8, output_limit_bytes=1024) == (
        "docker", "exec", "--interactive", "container-id", "python", "/opt/nova-runtime/nova_runtime.py",
        "execute", "--timeout-seconds", "8", "--output-limit-bytes", "1024",
    )
