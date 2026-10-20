"""Select a Phase 5 runtime backend without weakening the runsc default."""

from __future__ import annotations

import math

from .docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig
from .runtime_adapters import (
    FirecrackerRuntimeAdapter,
    FirecrackerRuntimeConfig,
    KataRuntimeAdapter,
    KataRuntimeConfig,
    SandboxRuntimeAdapter,
)
from .scheduling import KubernetesRuntimeAdapter, create_kubernetes_runtime_client
from .runtime_profile import DEFAULT_SANDBOX_RUNTIME_LIMITS


def create_runtime_adapter(
    *,
    backend: str,
    image: str,
    kernel_image: str | None = None,
    rootfs_image: str | None = None,
    client: object | None = None,
    namespace: str = "local",
    memory_mb: int = DEFAULT_SANDBOX_RUNTIME_LIMITS.memory_mb,
    cpu_cores: float = DEFAULT_SANDBOX_RUNTIME_LIMITS.cpu_cores,
    allow_local_image_id: bool = False,
) -> SandboxRuntimeAdapter:
    if type(memory_mb) is not int or memory_mb < 256:
        raise ValueError("sandbox memory_mb must be an integer of at least 256 MiB")
    if isinstance(cpu_cores, bool) or not isinstance(cpu_cores, (int, float)) or not math.isfinite(cpu_cores) or cpu_cores <= 0:
        raise ValueError("sandbox cpu_cores must be finite and positive")
    selected = backend.strip().lower()
    if selected in {"runsc", "gvisor", "docker"}:
        return DockerRuntimeAdapter(DockerRuntimeConfig(image=image, namespace=namespace, memory=f"{memory_mb}m", cpus=str(float(cpu_cores)), allow_local_image_id=allow_local_image_id))
    if selected in {"kata", "kata-qemu"}:
        return KataRuntimeAdapter(KataRuntimeConfig(image=image, namespace=namespace, memory=f"{memory_mb}m", cpus=str(float(cpu_cores))))
    if memory_mb != DEFAULT_SANDBOX_RUNTIME_LIMITS.memory_mb or cpu_cores != DEFAULT_SANDBOX_RUNTIME_LIMITS.cpu_cores:
        raise ValueError("sandbox resource overrides require a Docker-backed runtime")
    if selected in {"kubernetes", "k8s"}:
        return KubernetesRuntimeAdapter(image=image, client=client)
    if selected in {"firecracker", "fc"}:
        if not kernel_image or not rootfs_image:
            raise ValueError("Firecracker backend requires pinned kernel and rootfs images")
        return FirecrackerRuntimeAdapter(
            FirecrackerRuntimeConfig(kernel_image=kernel_image, rootfs_image=rootfs_image)
        )
    raise ValueError(f"unsupported Sandbox runtime backend: {backend}")
