"""Select a Phase 5 runtime backend without weakening the runsc default."""

from __future__ import annotations

from .docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig
from .runtime_adapters import (
    FirecrackerRuntimeAdapter,
    FirecrackerRuntimeConfig,
    KataRuntimeAdapter,
    KataRuntimeConfig,
    SandboxRuntimeAdapter,
)
from .scheduling import KubernetesRuntimeAdapter, create_kubernetes_runtime_client


def create_runtime_adapter(
    *,
    backend: str,
    image: str,
    kernel_image: str | None = None,
    rootfs_image: str | None = None,
    client: object | None = None,
) -> SandboxRuntimeAdapter:
    selected = backend.strip().lower()
    if selected in {"runsc", "gvisor", "docker"}:
        return DockerRuntimeAdapter(DockerRuntimeConfig(image=image))
    if selected in {"kata", "kata-qemu"}:
        return KataRuntimeAdapter(KataRuntimeConfig(image=image))
    if selected in {"kubernetes", "k8s"}:
        return KubernetesRuntimeAdapter(image=image, client=client)
    if selected in {"firecracker", "fc"}:
        if not kernel_image or not rootfs_image:
            raise ValueError("Firecracker backend requires pinned kernel and rootfs images")
        return FirecrackerRuntimeAdapter(
            FirecrackerRuntimeConfig(kernel_image=kernel_image, rootfs_image=rootfs_image)
        )
    raise ValueError(f"unsupported Sandbox runtime backend: {backend}")
