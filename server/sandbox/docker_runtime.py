"""Docker Runtime adapter used only by the future Sandbox Manager process.

The adapter intentionally builds a narrow, inspectable Docker CLI command.
The Web API never invokes it and must not receive a Docker socket.
"""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
import re
from uuid import uuid4

from .runtime_profile import DEFAULT_SANDBOX_RUNTIME_LIMITS


DOCKER_COMMAND_TIMEOUT_SECONDS = 15
PROCESS_REAP_TIMEOUT_SECONDS = 2


async def _kill_and_reap(process: asyncio.subprocess.Process) -> None:
    """Stop a Docker CLI child without waiting forever on inherited pipes."""
    try:
        process.kill()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=PROCESS_REAP_TIMEOUT_SECONDS)
    except (TimeoutError, ProcessLookupError):
        # The Docker daemon owns the container lifecycle.  A stuck CLI must
        # never hold the Manager event loop hostage; reconciliation can retry.
        pass


async def _communicate(process: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
    try:
        return await asyncio.wait_for(process.communicate(), timeout=DOCKER_COMMAND_TIMEOUT_SECONDS)
    except TimeoutError as error:
        await _kill_and_reap(process)
        raise TimeoutError("Docker sandbox command timed out") from error


async def _wait(process: asyncio.subprocess.Process) -> None:
    try:
        await asyncio.wait_for(process.wait(), timeout=DOCKER_COMMAND_TIMEOUT_SECONDS)
    except TimeoutError as error:
        await _kill_and_reap(process)
        raise TimeoutError("Docker sandbox command timed out") from error


@dataclass(frozen=True)
class DockerRuntimeConfig:
    image: str
    memory: str = f"{DEFAULT_SANDBOX_RUNTIME_LIMITS.memory_mb}m"
    cpus: str = str(DEFAULT_SANDBOX_RUNTIME_LIMITS.cpu_cores)
    pids_limit: int = DEFAULT_SANDBOX_RUNTIME_LIMITS.pids_limit
    workspace_size: str = f"{DEFAULT_SANDBOX_RUNTIME_LIMITS.workspace_mb}m"
    tmp_size: str = f"{DEFAULT_SANDBOX_RUNTIME_LIMITS.tmp_mb}m"
    shm_size: str = f"{DEFAULT_SANDBOX_RUNTIME_LIMITS.shm_mb}m"
    runtime: str = "runsc"
    allow_local_image_id: bool = False

    def __post_init__(self) -> None:
        local_image_id = re.fullmatch(r"sha256:[0-9a-fA-F]{64}", self.image) is not None
        if "@sha256:" not in self.image and not (local_image_id and self.allow_local_image_id):
            raise ValueError("sandbox runtime image must be pinned by immutable digest")
        if self.runtime != "runsc":
            raise ValueError("Phase 3 sandbox runtime must use gVisor runsc")


class DockerRuntimeAdapter:
    """Build hardened, unassigned warm-runtime containers for a single profile."""

    def __init__(self, config: DockerRuntimeConfig) -> None:
        self.config = config
        self.runtime_kind = "docker"

    @property
    def image_digest(self) -> str:
        """Expose immutable image identity without leaking backend config."""
        return self.config.image

    def create_command(self, *, name: str, claim_nonce: str) -> tuple[str, ...]:
        return (
            "docker", "run", "--detach", "--name", name,
            "--runtime", self.config.runtime,
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

    def create_l1_command(self, *, name: str) -> tuple[str, ...]:
        command = list(self.create_command(name=name, claim_nonce=""))
        command[1] = "create"
        command.remove("--detach")
        return tuple(command)

    async def image_cached(self) -> bool:
        process = await asyncio.create_subprocess_exec("docker", "image", "inspect", self.config.image)
        await _wait(process)
        return process.returncode == 0

    async def create_l1(self, *, name: str) -> str:
        process = await asyncio.create_subprocess_exec(*self.create_l1_command(name=name), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await _communicate(process)
        if process.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", "replace").strip())
        return stdout.decode("utf-8", "replace").strip()

    async def start_l1(self, external_runtime_id: str) -> None:
        process = await asyncio.create_subprocess_exec("docker", "start", external_runtime_id, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, stderr = await _communicate(process)
        if process.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", "replace").strip())

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
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await _communicate(process)
        if process.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", "replace").strip() or "Docker sandbox destroy failed")

    async def healthy(self, external_runtime_id: str) -> bool:
        process = await asyncio.create_subprocess_exec(
            "docker", "inspect", "--format", "{{.State.Running}}", external_runtime_id,
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await _communicate(process)
        return process.returncode == 0 and stdout.decode().strip().lower() == "true"

    async def usage(self, external_runtime_id: str) -> dict[str, float]:
        """Read current usage percentages without returning allocation details."""
        process = await asyncio.create_subprocess_exec(
            "docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}|{{.MemPerc}}",
            external_runtime_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await _communicate(process)
        if process.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", "replace").strip() or "Docker sandbox usage unavailable")
        try:
            cpu_raw, memory_raw = stdout.decode("utf-8", "replace").strip().split("|", 1)
            cpu_percent = float(cpu_raw.rstrip("%"))
            memory_percent = float(memory_raw.rstrip("%"))
        except (ValueError, UnicodeDecodeError) as error:
            raise RuntimeError("Docker sandbox returned invalid usage percentages") from error
        if cpu_percent < 0 or memory_percent < 0:
            raise RuntimeError("Docker sandbox returned invalid usage percentages")
        return {"cpu_percent": cpu_percent, "memory_percent": memory_percent}

    async def kernel_ready(self, external_runtime_id: str) -> bool:
        """Probe the actual Kernel protocol without exposing a container port."""
        if not await self.healthy(external_runtime_id):
            return False
        process = await asyncio.create_subprocess_exec(
            "docker", "exec", external_runtime_id,
            "python", "/opt/nova-runtime/nova_runtime.py", "health",
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
        self, external_runtime_id: str, *, source: str,
        timeout_seconds: int = DEFAULT_SANDBOX_RUNTIME_LIMITS.timeout_seconds,
        output_limit_bytes: int = DEFAULT_SANDBOX_RUNTIME_LIMITS.output_limit_bytes,
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

    async def run_scratch(
        self, *, source: str,
        timeout_seconds: int = DEFAULT_SANDBOX_RUNTIME_LIMITS.timeout_seconds,
        output_limit_bytes: int = DEFAULT_SANDBOX_RUNTIME_LIMITS.output_limit_bytes,
    ) -> dict[str, object]:
        """Run a model experiment in a fresh hardened process, never a user kernel."""
        if not 1 <= timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be between 1 and 60")
        name = f"nova-scratch-{uuid4().hex}"
        command = list(self.create_command(name=name, claim_nonce=""))
        command[2:3] = ["--rm"]
        command.insert(2, "--interactive")
        command.extend(("python", "/opt/nova-runtime/nova_runtime.py", "scratch", "--timeout-seconds", str(timeout_seconds), "--output-limit-bytes", str(output_limit_bytes)))
        process = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        payload = json.dumps({"source": source}).encode("utf-8")
        try:
            # Let asyncio own the stdin lifecycle.  Manually closing the pipe
            # before communicate() can race the subprocess transport and leave
            # the runtime reading an empty stream (JSON decode failure).
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=payload), timeout=timeout_seconds + 3
            )
        except TimeoutError as error:
            process.kill()
            await process.communicate()
            # Killing the local Docker CLI does not imply the detached daemon
            # stopped the container. Force removal is therefore mandatory on
            # the known, Manager-generated name.
            try:
                cleanup = await asyncio.create_subprocess_exec(
                    "docker", "rm", "--force", name,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await _communicate(cleanup)
            except Exception:
                # The original timeout remains authoritative; reconciliation
                # will also find any managed container that survived cleanup.
                pass
            raise TimeoutError("sandbox scratch execution timed out") from error
        if process.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", "replace").strip())
        return json.loads(stdout.decode("utf-8"))

    async def managed_runtime_ids(self) -> set[str]:
        """List only Manager-owned containers; never enumerate arbitrary Docker work."""
        process = await asyncio.create_subprocess_exec(
            "docker", "ps", "--all", "--no-trunc", "--quiet", "--filter", "label=nova.sandbox.managed=true",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await _communicate(process)
        if process.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", "replace").strip())
        return {line for line in stdout.decode("utf-8", "replace").splitlines() if line}
