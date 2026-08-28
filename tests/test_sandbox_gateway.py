from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch


def test_docker_open_commits_lease_before_manager_claim() -> None:
    from server.sandbox.contracts import SandboxScope
    from server.sandbox.gateway import SandboxGateway
    from server.sandbox.ticket import SandboxTicketSigner

    scope = SandboxScope(
        owner_user_id="user-1",
        auth_session_id="session-1",
        workspace_id="workspace-1",
        generation=1,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    class Transaction:
        def __init__(self, factory):
            self.factory = factory

        async def __aenter__(self):
            self.factory.begin_calls += 1
            return self.factory.committed_session

        async def __aexit__(self, *_args):
            self.factory.committed = True

    class Factory:
        begin_calls = 0
        committed = False
        committed_session = object()

        def begin(self):
            return Transaction(self)

    class Manager:
        async def claim(self, _scope, *, lease_id):
            assert lease_id == "lease-1"
            return None

    factory = Factory()
    gateway = SandboxGateway(
        mode="docker",
        session_factory=factory,
        ticket_signer=SandboxTicketSigner("gateway-test-secret"),
        manager=Manager(),
    )

    async def fake_ensure(session, _scope):
        assert session is factory.committed_session
        return {"lease": {"id": "lease-1"}}

    async def exercise():
        with patch(
            "server.sandbox.gateway.sandbox_lifecycle_service.ensure_current_lease",
            new=fake_ensure,
        ):
            return await gateway.open(object(), scope)

    result = asyncio.run(exercise())
    assert result["pool_status"] == "warming"
    assert factory.begin_calls == 1
    assert factory.committed is True
