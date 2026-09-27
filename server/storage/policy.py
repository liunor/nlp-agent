"""Small, pure policy module for account storage quotas.

The policy is deliberately independent from SQLAlchemy and FastAPI so the
quota contract can be exercised without a database or filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StorageBucket(StrEnum):
    CORE = "core"
    FILES = "files"


@dataclass(frozen=True, slots=True)
class StoragePolicy:
    core_quota_bytes: int
    files_quota_bytes: int
    max_file_bytes: int
    max_items: int

    @property
    def total_quota_bytes(self) -> int:
        return self.core_quota_bytes + self.files_quota_bytes

    def quota_for(self, bucket: StorageBucket) -> int:
        return self.core_quota_bytes if bucket is StorageBucket.CORE else self.files_quota_bytes


_MIB = 1024 * 1024

# Quotas are intentionally modest. They are logical upper bounds and are not
# pre-allocated on the 100 GiB host.
ROLE_POLICIES: dict[str, StoragePolicy] = {
    "guest": StoragePolicy(32 * _MIB, 16 * _MIB, 5 * _MIB, 50),
    "student": StoragePolicy(128 * _MIB, 128 * _MIB, 10 * _MIB, 500),
    "teacher": StoragePolicy(256 * _MIB, 512 * _MIB, 25 * _MIB, 2_000),
    "developer": StoragePolicy(256 * _MIB, 512 * _MIB, 25 * _MIB, 2_000),
    "admin": StoragePolicy(256 * _MIB, 512 * _MIB, 25 * _MIB, 2_000),
}

_ROLE_PRIORITY = ("guest", "student", "teacher", "developer", "admin")


def policy_for_roles(roles: set[str] | frozenset[str] | list[str] | tuple[str, ...]) -> StoragePolicy:
    """Return the highest policy represented by a user's roles.

    Roles are not additive. A user with student + teacher permissions receives
    the teacher policy once, which prevents accidental quota multiplication.
    Unknown roles fall back to the guest policy.
    """

    role_set = set(roles)
    for role in reversed(_ROLE_PRIORITY):
        if role in role_set:
            return ROLE_POLICIES[role]
    return ROLE_POLICIES["guest"]


def usage_ratio(used_bytes: int, quota_bytes: int) -> float:
    """Return a clamped ratio suitable for a progress bar."""

    if quota_bytes <= 0:
        return 1.0
    return min(1.0, max(0.0, used_bytes / quota_bytes))


def usage_state(used_bytes: int, quota_bytes: int) -> str:
    """Return the UI state used for Windows-like capacity colors."""

    ratio = usage_ratio(used_bytes, quota_bytes)
    if ratio >= 1:
        return "full"
    if ratio >= 0.9:
        return "critical"
    if ratio >= 0.8:
        return "warning"
    return "normal"
