"""Docker Runtime adapter used only by the future Sandbox Manager process.

The adapter intentionally builds a narrow, inspectable Docker CLI command.
The Web API never invokes it and must not receive a Docker socket.
"""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json


DOCKER_COMMAND_TIMEOUT_SECONDS = 15


async def _communicate(process: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
    try:
        return await asyncio.wait_for(process.communicate(), timeout=DOCKER_COMMAND_TIMEOUT_SECONDS)
    except TimeoutError as error:
        process.kill()
        await process.communicate()
        raise TimeoutError("Docker sandbox command timed out") from error


async def _wait(process: asyncio.subprocess.Process) -> None:
    try:
        await asyncio.wait_for(process.wait(), timeout=DOCKER_COMMAND_TIMEOUT_SECONDS)
    except TimeoutError as error:
        process.kill()
        await process.wait()
        raise TimeoutError("Docker sandbox command timed out") from error


@dataclass(frozen=True)
class DockerRuntimeConfig:
    image: str
    memory: str = "768m"
    cpus: str = "1.0"
    pids_limit: int = 128
    workspace_size: str = "256m"
    tmp_size: str = "256m"
    shm_size: str = "64m"

    def __post_init__(self) -> None:
        if "@sha256:" not in self.image:
            raise ValueError("sandbox runtime image must be pinned by immutable digest")


class DockerRuntimeAdapter:
    """Build hardened, unassigned warm-runtime containers for a single profile."""

    def __init__(self, config: DockerRuntimeConfig) -> None:
        self.config = config

    def create_command(self, *, name: str, claim_nonce: str) -> tuple[str, ...]:
        return (
            "docker", "run", "--detach", "--name", name,
            "--label", "nova.sandbox.managed=true",
            "--label", "nova.sandbox.state=ready_unbound",
            "--read-only", "--network", "none", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges=true",
            "--memory", self.config.memory, "--cpus", self.config.cpus,
            "--pids-limit", str(self.config.pids_limit), "--shm-size", self.config.shm_size,
            "--tmpfs", f"/workspace:rw,nosuid,nodev,size={self.config.workspace_size}",
            "--tmpfs", f"/tmp:rw,nosuid,nodev,size={self.config.tmp_size}",
            "--tmpfs", "/run/nova:rw,nosuid,nodev,uid=10001,gid=10001,mode=700,size=16m",
            "--user", "10001:10001", self.config.image,
        )

    async def create_ready(self, *, name: str, claim_nonce: str) -> str:
        del claim_nonce  # persisted only as a DB hash; never put it in Docker metadata.
        process = await asyncio.create_subprocess_exec(
            *self.create_command(name=name, claim_nonce=""),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await _communicate(process)
        if process.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", "replace").strip())
        return stdout.decode("utf-8", "replace").strip()

    async def destroy(self, external_runtime_id: str) -> None:
        process = await asyncio.create_subprocess_exec(
            "docker", "rm", "--force", external_runtime_id,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await _wait(process)

    async def healthy(self, external_runtime_id: str) -> bool:
        process = await asyncio.create_subprocess_exec(
            "docker", "inspect", "--format", "{{.State.Running}}", external_runtime_id,
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await _communicate(process)
        return process.returncode == 0 and stdout.decode().strip().lower() == "true"

    async def kernel_ready(self, external_runtime_id: str) -> bool:
        """Check the runtime's actual kernel endpoint without exposing its port."""
        if not await self.healthy(external_runtime_id):
            return False
        process = await asyncio.create_subprocess_exec(
            "docker", "exec", external_runtime_id, "test", "-s", "/run/nova/kernel.json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await _communicate(process)
        return process.returncode == 0

    @staticmethod
    def execute_command(
        external_runtime_id: str, *, timeout_seconds: int, output_limit_bytes: int
    ) -> tuple[str, ...]:
        return (
            "docker", "exec", "--interactive", external_runtime_id,
            "python", "/opt/nova-runtime/nova_runtime.py", "execute",
            "--timeout-seconds", str(timeout_seconds),
            "--output-limit-bytes", str(output_limit_bytes),
        )

    async def execute(
        self, external_runtime_id: str, *, source: str, timeout_seconds: int = 15,
        output_limit_bytes: int = 1_000_000,
    ) -> dict[str, object]:
        """Send code through stdin, never through argv, labels, or Docker metadata."""
        if not 1 <= timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be between 1 and 60")
        if not 1 <= output_limit_bytes <= 1_000_000:
            raise ValueError("output_limit_bytes must be between 1 and 1000000")
        process = await asyncio.create_subprocess_exec(
            *self.execute_command(
                external_runtime_id, timeout_seconds=timeout_seconds,
                output_limit_bytes=output_limit_bytes,
            ),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdin is not None
        process.stdin.write(json.dumps({"source": source}).encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds + 3)
        except TimeoutError as error:
            process.kill()
            await process.communicate()
            await self.interrupt(external_runtime_id)
            raise TimeoutError("sandbox execution timed out") from error
        if process.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", "replace").strip())
        try:
            payload = json.loads(stdout.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise RuntimeError("sandbox runtime returned invalid protocol JSON") from error
        if not isinstance(payload, dict):
            raise RuntimeError("sandbox runtime returned an invalid payload")
        return payload

    async def interrupt(self, external_runtime_id: str) -> None:
        process = await asyncio.create_subprocess_exec(
            "docker", "exec", external_runtime_id,
            "python", "/opt/nova-runtime/nova_runtime.py", "interrupt",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await _wait(process)

    async def managed_runtime_ids(self) -> set[str]:
        """List only Manager-owned containers; never enumerate arbitrary Docker work."""
        process = await asyncio.create_subprocess_exec(
            "docker", "ps", "--all", "--quiet", "--filter", "label=nova.sandbox.managed=true",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await _communicate(process)
        if process.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", "replace").strip())
        return {line for line in stdout.decode("utf-8", "replace").splitlines() if line}
