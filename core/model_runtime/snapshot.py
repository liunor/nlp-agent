"""Secret-free immutable execution parameters captured at Turn admission."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field, model_validator

from core.model_runtime.contracts import FrozenModel, ModelPresetConfig
from core.model_runtime.thinking import ThinkingLevel


class FixedTurnBillingSnapshot(FrozenModel):
    billing_mode: Literal["fixed_turn"] = "fixed_turn"
    thinking_level: ThinkingLevel
    policy_version: str = Field(min_length=1)
    amount_micro: int = Field(strict=True, gt=0)
    reservation_id: str = Field(min_length=1)


class SnapshotPreset(FrozenModel):
    preset_name: str
    preset: ModelPresetConfig


class ModelExecutionSnapshot(FrozenModel):
    version: Literal[1] = 1
    mapping_version: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    profile_name: str
    level: ThinkingLevel
    worker_override: bool = False
    billing: FixedTurnBillingSnapshot | None = None
    # Canonical JSON keeps nested runtime dictionaries immutable too. API keys
    # and fixed headers are excluded; credentials are resolved only at execution.
    runtime_json: str
    coordinator: tuple[SnapshotPreset, ...] = Field(min_length=1)
    worker: tuple[SnapshotPreset, ...] = Field(min_length=1)
    utility: tuple[SnapshotPreset, ...] = Field(min_length=1)
    system_worker: tuple[SnapshotPreset, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_charge_level(self):
        if self.billing is not None and self.billing.thinking_level != self.level:
            raise ValueError("Charge level differs from execution level")
        return self

    def execution_key(self) -> str:
        execution = self.model_dump_json(
            exclude={"user_id", "workspace_id", "session_id", "turn_id", "billing"}
        )
        return hashlib.sha256(execution.encode("utf-8")).hexdigest()

    def role_presets(self, role: str) -> tuple[SnapshotPreset, ...]:
        if role not in {"coordinator", "worker", "utility", "system_worker"}:
            raise ValueError(f"Unknown snapshot role {role!r}")
        return getattr(self, role)

    def require_owner(
        self, *, user_id: str, workspace_id: str, session_id: str, turn_id: str
    ) -> None:
        if (self.user_id, self.workspace_id, self.session_id, self.turn_id) != (
            user_id,
            workspace_id,
            session_id,
            turn_id,
        ):
            raise ValueError("Model execution snapshot does not belong to this Turn")
