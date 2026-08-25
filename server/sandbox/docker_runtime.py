"""Docker Runtime adapter used only by the future Sandbox Manager process.

The adapter intentionally builds a narrow, inspectable Docker CLI command.
The Web API never invokes it and must not receive a Docker socket.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DockerRuntimeConfig:
    image: str
    memory: str = "768m"
    cpus: str = "1.0"
    pids_limit: int = 128
    workspace_size: str = "256m"
    tmp_size: str = "256m"
    shm_size: str = "64m"


class DockerRuntimeAdapter:
    """Build hardened, unassigned warm-runtime containers for a single profile."""

    def __init__(self, config: DockerRuntimeConfig) -> None:
        self.config = config

    def create_command(self, *, name: str, claim_nonce: str) -> tuple[str, ...]:
        return (
            "docker", "run", "--detach", "--name", name,
            "--label", "nova.sandbox.state=ready_unbound",
            "--label", f"nova.sandbox.claim_nonce={claim_nonce}",
            "--read-only", "--network", "none", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges=true",
            "--memory", self.config.memory, "--cpus", self.config.cpus,
            "--pids-limit", str(self.config.pids_limit), "--shm-size", self.config.shm_size,
            "--tmpfs", f"/workspace:rw,nosuid,nodev,size={self.config.workspace_size}",
            "--tmpfs", f"/tmp:rw,nosuid,nodev,size={self.config.tmp_size}",
            "--tmpfs", "/run/nova:rw,nosuid,nodev,uid=10001,gid=10001,mode=700,size=16m",
            "--user", "10001:10001", self.config.image,
        )
