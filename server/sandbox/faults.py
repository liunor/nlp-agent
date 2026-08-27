"""Opt-in, named fault injection for Sandbox recovery tests."""

from __future__ import annotations

from dataclasses import dataclass
import os


class SandboxInjectedFault(RuntimeError):
    """A deterministic failure requested by an integration test/operator."""


@dataclass(frozen=True, slots=True)
class SandboxFaultInjector:
    stages: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls, value: str | None = None) -> "SandboxFaultInjector":
        raw = os.getenv("NLP_AGENT_SANDBOX_FAULT_INJECTION", "") if value is None else value
        return cls(frozenset(item.strip() for item in raw.split(",") if item.strip()))

    def fail_if_configured(self, stage: str) -> None:
        if stage in self.stages or "*" in self.stages:
            raise SandboxInjectedFault(f"sandbox fault injected at {stage}")
