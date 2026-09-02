"""Deterministic user/workspace rollout for quota enforcement."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable


def _values(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        return frozenset(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, Iterable):
        return frozenset(str(item).strip() for item in value if str(item).strip())
    return frozenset()


@dataclass(frozen=True)
class QuotaRollout:
    """A stable rollout decision shared by Gateway, Web and Worker startup."""

    global_enabled: bool = False
    percentage: int = 0
    user_ids: frozenset[str] = frozenset()
    workspace_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not 0 <= self.percentage <= 100:
            raise ValueError("quota rollout percentage must be between 0 and 100")

    @property
    def configured(self) -> bool:
        return bool(
            self.global_enabled
            or self.percentage
            or self.user_ids
            or self.workspace_ids
        )

    def enabled_for(self, user_id: str, workspace_id: str | None = None) -> bool:
        if user_id in self.user_ids or (
            workspace_id is not None and workspace_id in self.workspace_ids
        ):
            return True
        if self.global_enabled and self.percentage == 0:
            return True
        if self.percentage == 0:
            return False
        if self.percentage >= 100:
            return True
        key = f"{workspace_id or ''}:{user_id}".encode("utf-8")
        bucket = int(hashlib.sha256(key).hexdigest()[:8], 16) % 100
        return bucket < self.percentage

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any],
        *,
        global_enabled: bool = False,
    ) -> "QuotaRollout":
        percentage_value = config.get("quota_enforcement_percentage")
        configured_percentage = (
            None if percentage_value is None else int(percentage_value)
        )
        percentage = (
            100
            if global_enabled and (configured_percentage is None or configured_percentage == 0)
            else (configured_percentage or 0)
        )
        return cls(
            global_enabled=global_enabled,
            percentage=percentage,
            user_ids=_values(config.get("quota_enforcement_users")),
            workspace_ids=_values(config.get("quota_enforcement_workspaces")),
        )
