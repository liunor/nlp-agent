from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest


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


def test_open_exposes_the_current_runtime_profile_for_the_workbench() -> None:
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

    async def _warming_claim():
        return None

    class WarmingManager:
        async def claim(self, *_args, **_kwargs):
            return await _warming_claim()

    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    class SessionFactory:
        def begin(self):
            return Transaction()

    gateway = SandboxGateway(
        mode="docker",
        session_factory=SessionFactory(),
        ticket_signer=SandboxTicketSigner("gateway-profile-test-secret"),
        manager=WarmingManager(),
    )

    async def fake_ensure(_session, _scope):
        return {
            "environment": {"id": "environment-1", "profile": "python-base"},
            "lease": {"id": "lease-1"},
        }

    async def exercise():
        with patch(
            "server.sandbox.gateway.sandbox_lifecycle_service.ensure_current_lease",
            new=fake_ensure,
        ):
            return await gateway.open(object(), scope)

    # A warming pool still needs to tell the user what the eventual runtime
    # will be constrained by; this is independent of whether a slot was claimed.
    result = asyncio.run(exercise())

    assert result["runtime_profile"] == {
        "id": "python-base",
        "runtime": "runsc",
        "isolation": "runsc 隔离",
        "python_version": "3.11",
        "kernel_version": "6.29.5",
        "pytorch_version": "2.7.1",
        "pytorch_device": "CPU",
    }
    assert not any(key.endswith("_mb") or key.endswith("_cores") for key in result["runtime_profile"])


def test_inmemory_runtime_usage_is_explicitly_unavailable_without_fake_limits() -> None:
    from server.sandbox.contracts import SandboxScope
    from server.sandbox.gateway import SandboxGateway
    from server.sandbox.inmemory_runtime import InMemoryRuntime
    from server.sandbox.ticket import SandboxTicketSigner

    scope = SandboxScope(
        owner_user_id="user-1",
        auth_session_id="session-1",
        workspace_id="workspace-1",
        generation=1,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    gateway = SandboxGateway(
        mode="inmemory",
        session_factory=object(),
        ticket_signer=SandboxTicketSigner("gateway-usage-test-secret"),
        inmemory=InMemoryRuntime(),
    )

    result = asyncio.run(gateway.runtime_usage(scope, ticket=None))

    assert result == {"cpu_percent": None, "memory_percent": None, "sampled_at": None}


@pytest.mark.parametrize("nonce", [None, "one-shot-claim"])
def test_usage_renews_valid_ticket_after_authorization_without_rotating_nonce(nonce) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from server.sandbox.contracts import SandboxScope
    from server.sandbox.gateway import SandboxGateway
    from server.sandbox.ticket import SandboxTicketClaims, SandboxTicketSigner

    signer = SandboxTicketSigner("usage-renewal-secret")
    claims = SandboxTicketClaims("user", "session", "lease", "runtime", 3, nonce)
    scope = SandboxScope("user", "session", "workspace", 3, datetime.now(UTC) + timedelta(minutes=30))
    manager = SimpleNamespace(runtime_usage=AsyncMock(return_value={"cpu_percent": 12.0, "memory_percent": 24.0}))
    gateway = SandboxGateway(mode="docker", session_factory=object(), ticket_signer=signer, manager=manager)
    with patch("server.sandbox.ticket.time.time", return_value=1000):
        original = signer.issue(claims)
    with patch("server.sandbox.ticket.time.time", return_value=1290):
        result = asyncio.run(gateway.runtime_usage(scope, ticket=original))
    manager.runtime_usage.assert_awaited_once_with(scope, lease_id="lease", runtime_id="runtime", generation=3)
    with patch("server.sandbox.ticket.time.time", return_value=1301):
        with pytest.raises(PermissionError, match="expired"):
            signer.verify(original, user_id="user", auth_session_id="session")
        assert signer.verify(result["ticket"], user_id="user", auth_session_id="session") == claims
    assert result["cpu_percent"] == 12.0
    assert result["memory_percent"] == 24.0


@pytest.mark.parametrize("failure", ["expired", "other-session", "revoked"])
def test_usage_never_renews_expired_cross_session_or_revoked_access(failure) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from server.sandbox.contracts import SandboxScope
    from server.sandbox.gateway import SandboxGateway
    from server.sandbox.ticket import SandboxTicketClaims, SandboxTicketSigner

    signer = SandboxTicketSigner("usage-rejection-secret")
    claims = SandboxTicketClaims("user", "session", "lease", "runtime", 3, None)
    scope = SandboxScope("user", "session", "workspace", 3, datetime.now(UTC) + timedelta(minutes=30))
    with patch("server.sandbox.ticket.time.time", return_value=1000):
        ticket = signer.issue(claims)
    signer.issue = Mock(wraps=signer.issue)
    manager = SimpleNamespace(runtime_usage=AsyncMock(side_effect=PermissionError("revoked")))
    gateway = SandboxGateway(mode="docker", session_factory=object(), ticket_signer=signer, manager=manager)
    if failure == "other-session":
        scope = SandboxScope("user", "different-session", "workspace", 3, datetime.now(UTC) + timedelta(minutes=30))
    with patch("server.sandbox.ticket.time.time", return_value=1301 if failure == "expired" else 1100):
        with pytest.raises(PermissionError):
            asyncio.run(gateway.runtime_usage(scope, ticket=ticket))
    signer.issue.assert_not_called()
    if failure == "revoked":
        manager.runtime_usage.assert_awaited_once()
    else:
        manager.runtime_usage.assert_not_awaited()
