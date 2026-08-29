"""Stable, user-safe metadata for the Python sandbox runtime.

Resource limits remain a Manager-side implementation detail.  The web
workbench receives runtime versions and sampled usage percentages instead of
revealing the container's allocation or storage ceilings.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


PYTHON_VERSION = "3.11"
IPYKERNEL_VERSION = "6.29.5"
PYTORCH_VERSION = "2.7.1"
PYTORCH_DEVICE = "CPU"


@dataclass(frozen=True, slots=True)
class SandboxRuntimeLimits:
    """Manager-only limits used when constructing a hardened container."""

    cpu_cores: float
    memory_mb: int
    workspace_mb: int
    tmp_mb: int
    shm_mb: int
    pids_limit: int
    timeout_seconds: int
    output_limit_bytes: int


@dataclass(frozen=True, slots=True)
class SandboxRuntimeProfile:
    """User-facing runtime identity, never its allocation limits."""

    id: str
    runtime: str
    isolation: str
    python_version: str = PYTHON_VERSION
    kernel_version: str = IPYKERNEL_VERSION
    pytorch_version: str = PYTORCH_VERSION
    pytorch_device: str = PYTORCH_DEVICE

    def public_payload(self) -> dict[str, object]:
        """Return only safe, stable values for the web API."""

        return {
            "id": self.id,
            "runtime": self.runtime,
            "isolation": self.isolation,
            "python_version": self.python_version,
            "kernel_version": self.kernel_version,
            "pytorch_version": self.pytorch_version,
            "pytorch_device": self.pytorch_device,
        }


DEFAULT_SANDBOX_RUNTIME_LIMITS = SandboxRuntimeLimits(
    cpu_cores=1.0,
    memory_mb=768,
    workspace_mb=256,
    tmp_mb=256,
    shm_mb=64,
    pids_limit=128,
    timeout_seconds=15,
    output_limit_bytes=1_000_000,
)


DEFAULT_SANDBOX_RUNTIME_PROFILE = SandboxRuntimeProfile(
    id="python-base",
    runtime="runsc",
    isolation="runsc 隔离",
)


def public_runtime_profile(
    profile_id: str = "python-base", *, mode: str = "docker", backend: str = "runsc"
) -> dict[str, object]:
    """Build the user-visible profile without exposing runtime capabilities.

    ``python-base`` is currently the only schedulable profile.  Keeping the
    profile id in this function makes adding another profile an explicit,
    reviewable change instead of silently showing the wrong limits.
    """

    profile = replace(DEFAULT_SANDBOX_RUNTIME_PROFILE, id=profile_id)
    if mode.strip().lower() == "inmemory":
        return replace(
            profile,
            runtime="inmemory",
            isolation="开发预览",
        ).public_payload()

    backend_name = backend.strip().lower()
    runtime = {
        "docker": "runsc",
        "gvisor": "runsc",
        "runsc": "runsc",
        "kata": "kata-qemu",
        "kata-qemu": "kata-qemu",
        "kubernetes": "kubernetes",
        "k8s": "kubernetes",
        "firecracker": "firecracker",
        "fc": "firecracker",
    }.get(backend_name, backend_name or profile.runtime)
    return replace(profile, runtime=runtime, isolation=f"{runtime} 隔离").public_payload()
