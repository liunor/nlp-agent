"""Safety gate for future runtime snapshots.

Snapshots stay disabled until the backend proves clean state and entropy
re-seeding.  This module intentionally contains no Docker/Kata/Firecracker
snapshot command, so enabling the flag cannot silently weaken isolation.
"""

from __future__ import annotations

from dataclasses import dataclass

from configs.settings import settings


@dataclass(frozen=True, slots=True)
class SnapshotCapability:
    backend: str
    clean: bool
    entropy_reseeded: bool


class RuntimeSnapshotPolicy:
    def __init__(self, *, enabled: bool = False, allowed_backends: tuple[str, ...] = ("runsc", "kata-qemu", "firecracker")) -> None:
        self.enabled = enabled
        self.allowed_backends = frozenset(allowed_backends)

    def authorize(self, capability: SnapshotCapability) -> bool:
        if not self.enabled:
            raise PermissionError("runtime snapshots are disabled")
        if capability.backend not in self.allowed_backends:
            raise PermissionError("runtime backend has no approved snapshot contract")
        if not capability.clean or not capability.entropy_reseeded:
            raise PermissionError("runtime snapshot safety gate is not satisfied")
        return True


def configured_snapshot_policy() -> RuntimeSnapshotPolicy:
    return RuntimeSnapshotPolicy(enabled=settings.NLP_AGENT_SANDBOX_SNAPSHOTS_ENABLED)
