from __future__ import annotations


def test_docker_runtime_command_has_no_host_or_network_escape_hatches() -> None:
    from server.sandbox.docker_runtime import DockerRuntimeConfig, DockerRuntimeAdapter

    command = DockerRuntimeAdapter(DockerRuntimeConfig(image="nova-sandbox-runtime:local")).create_command(
        name="nova-warm-python-base-1", claim_nonce="nonce"
    )

    assert command[:3] == ("docker", "run", "--detach")
    assert "--read-only" in command
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--pids-limit") + 1] == "128"
    assert all("docker.sock" not in item for item in command)
    assert "--volume" not in command
    assert "/run/nova:rw,nosuid,nodev,uid=10001,gid=10001,mode=700,size=16m" in command
    assert command[-1] == "nova-sandbox-runtime:local"
