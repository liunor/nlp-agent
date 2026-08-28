"""Opt-in Phase 5 runtime adapters.

The Phase 3 ``DockerRuntimeAdapter`` remains the production default.  Kata is
kept wire-compatible with the hardened Docker contract, while Firecracker is a
command/configuration adapter until a guest-agent integration is explicitly
enabled on a Linux deployment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .docker_runtime import DockerRuntimeAdapter


@runtime_checkable
class SandboxRuntimeAdapter(Protocol):
    """Complete lifecycle boundary consumed by the isolated Manager."""

    runtime_kind: str
    image_digest: str

    async def image_cached(self) -> bool: ...
    async def create_l1(self, *, name: str) -> str: ...
    async def start_l1(self, external_runtime_id: str) -> None: ...

    async def create_ready(self, *, name: str, claim_nonce: str) -> str: ...
    async def destroy(self, external_runtime_id: str) -> None: ...
    async def healthy(self, external_runtime_id: str) -> bool: ...
    async def kernel_ready(self, external_runtime_id: str) -> bool: ...
    async def execute(self, external_runtime_id: str, *, source: str) -> dict[str, object]: ...
    async def interrupt(self, external_runtime_id: str) -> None: ...
    async def managed_runtime_ids(self) -> set[str]: ...
    async def run_scratch(
        self,
        *,
        source: str,
        timeout_seconds: int = 15,
        output_limit_bytes: int = 1_000_000,
    ) -> dict[str, object]: ...


@dataclass(frozen=True)
class KataRuntimeConfig:
    image: str
    memory: str = "768m"
    cpus: str = "1.0"
    pids_limit: int = 128
    workspace_size: str = "256m"
    tmp_size: str = "256m"
    shm_size: str = "64m"
    runtime: str = "kata-qemu"

    def __post_init__(self) -> None:
        if "@sha256:" not in self.image:
            raise ValueError("Kata sandbox image must be pinned by immutable digest")
        if self.runtime != "kata-qemu":
            raise ValueError("Kata adapter only supports the kata-qemu runtime")
        if self.pids_limit < 1:
            raise ValueError("Kata pids_limit must be positive")


class KataRuntimeAdapter(DockerRuntimeAdapter):
    """Kata Containers adapter reusing the hardened Docker protocol."""

    config: KataRuntimeConfig

    def __init__(self, config: KataRuntimeConfig) -> None:
        self.config = config
        self.runtime_kind = "kata"


def _pinned_guest_ref(value: str, name: str) -> None:
    if "@sha256:" not in value:
        raise ValueError(f"{name} must be pinned by immutable digest")


@dataclass(frozen=True)
class FirecrackerRuntimeConfig:
    kernel_image: str
    rootfs_image: str
    memory_mib: int = 768
    vcpu_count: int = 1
    firecracker_binary: str = "firecracker"
    jailer_binary: str = "jailer"

    @property
    def image(self) -> str:
        """Expose the immutable rootfs as the Manager's image-digest field."""
        return self.rootfs_image

    def __post_init__(self) -> None:
        _pinned_guest_ref(self.kernel_image, "Firecracker kernel image")
        _pinned_guest_ref(self.rootfs_image, "Firecracker rootfs image")
        if self.memory_mib < 128 or self.vcpu_count < 1:
            raise ValueError("Firecracker memory/vcpu settings are invalid")


class FirecrackerRuntimeAdapter:
    """Firecracker launch contract; guest-agent execution is intentionally opt-in.

    Firecracker is not a Docker-compatible backend.  Keeping its launch and
    guest configuration explicit prevents silently treating a VM as a
    container until the guest agent, cleanup, and lifecycle tests are deployed.
    """

    def __init__(self, config: FirecrackerRuntimeConfig) -> None:
        self.config = config
        self.runtime_kind = "firecracker"

    @property
    def image_digest(self) -> str:
        return self.config.rootfs_image

    async def image_cached(self) -> bool:
        return False

    def launch_command(self, *, runtime_id: str) -> tuple[str, ...]:
        if not runtime_id or any(char in runtime_id for char in " /\\"):
            raise ValueError("invalid Firecracker runtime id")
        return (
            self.config.jailer_binary,
            "--id",
            runtime_id,
            "--exec-file",
            self.config.firecracker_binary,
            "--",
            "--api-sock",
            f"/run/firecracker/{runtime_id}.sock",
        )

    def guest_config(self) -> dict[str, object]:
        return {
            "boot-source": {"kernel_image": self.config.kernel_image, "boot_args": "console=ttyS0 reboot=k panic=1"},
            "drives": [{"drive_id": "rootfs", "path_on_host": self.config.rootfs_image, "is_root_device": True, "is_read_only": True}],
            "machine-config": {"vcpu_count": self.config.vcpu_count, "mem_size_mib": self.config.memory_mib},
        }

    async def create_ready(self, *, name: str, claim_nonce: str) -> str:
        del name, claim_nonce
        raise RuntimeError("Firecracker guest-agent lifecycle is not enabled")

    async def create_l1(self, *, name: str) -> str:
        del name
        raise RuntimeError("Firecracker guest-agent lifecycle is not enabled")

    async def start_l1(self, external_runtime_id: str) -> None:
        del external_runtime_id
        raise RuntimeError("Firecracker guest-agent lifecycle is not enabled")

    async def run_scratch(self, *, source: str, timeout_seconds: int = 15, output_limit_bytes: int = 1_000_000) -> dict[str, object]:
        del source, timeout_seconds, output_limit_bytes
        raise RuntimeError("Firecracker guest-agent lifecycle is not enabled")

    async def destroy(self, external_runtime_id: str) -> None:
        del external_runtime_id
        raise RuntimeError("Firecracker guest-agent lifecycle is not enabled")

    async def healthy(self, external_runtime_id: str) -> bool:
        del external_runtime_id
        return False

    async def kernel_ready(self, external_runtime_id: str) -> bool:
        del external_runtime_id
        return False

    async def execute(self, external_runtime_id: str, *, source: str) -> dict[str, object]:
        del external_runtime_id, source
        raise RuntimeError("Firecracker guest-agent lifecycle is not enabled")

    async def interrupt(self, external_runtime_id: str) -> None:
        del external_runtime_id
        raise RuntimeError("Firecracker guest-agent lifecycle is not enabled")

    async def managed_runtime_ids(self) -> set[str]:
        return set()
