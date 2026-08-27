from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest


class _RecordingSession:
    def __init__(self) -> None:
        self.pending: list[object] = []
        self.flushed_batches: list[list[type[object]]] = []

    def add(self, value: object) -> None:
        self.pending.append(value)

    async def flush(self) -> None:
        self.flushed_batches.append([type(value) for value in self.pending])
        self.pending.clear()


@pytest.mark.asyncio
async def test_manager_claim_probe_flushes_parent_rows_before_lease() -> None:
    from server.infrastructure.mysql.models import (
        SandboxEnvironmentModel,
        SandboxLeaseModel,
        SessionModel,
    )
    from scripts.benchmark_sandbox_startup import seed_manager_claim_probe_records

    session = _RecordingSession()
    now = datetime.now(UTC).replace(tzinfo=None)

    await seed_manager_claim_probe_records(
        session,
        user=SimpleNamespace(id="user-id", authorization_version=1),
        workspace_id="workspace-id",
        session_id="session-id",
        environment_id="environment-id",
        lease_id="lease-id",
        now=now,
    )

    assert session.flushed_batches == [
        [SessionModel, SandboxEnvironmentModel],
        [SandboxLeaseModel],
    ]


def test_manager_claim_probe_image_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.benchmark_sandbox_startup import manager_claim_probe_image

    monkeypatch.setenv("NLP_AGENT_SANDBOX_MANAGER_TEST_IMAGE", "nova-sandbox-runtime:manager-ci")

    assert manager_claim_probe_image() == "nova-sandbox-runtime:manager-ci"
