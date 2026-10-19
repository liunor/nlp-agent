"""Password CPU work must not consume the pool needed by existing sessions."""
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from types import SimpleNamespace
from unittest.mock import AsyncMock
import threading
import time

import pytest

from server.web.auth import AuthenticationError
from server.web.database_auth import DatabaseSessionAuth


class Factory:
    def __init__(self):
        self.user = SimpleNamespace(id="fixture-user", password_hash="original-hash",
                                    status="active", deleted_at=None, authorization_version=1)
        self.pool = asyncio.Semaphore(2)
        self.active = 0
        self.connection_held = ContextVar("fixture_connection_held",default=False)
        self.rows = []
        self.updates = []

    @asynccontextmanager
    async def begin(self):
        async with self.pool:
            self.active += 1
            token = self.connection_held.set(True)
            try:
                yield self
            finally:
                self.connection_held.reset(token)
                self.active -= 1

    async def scalar(self, statement):
        assert statement.is_select
        return self.user if self.user is not None and self.user.deleted_at is None else None

    def add(self, row):
        self.rows.append(row)

    async def execute(self, statement):
        self.updates.append(statement)

    async def flush(self):
        pass


@pytest.fixture
def case():
    factory = Factory()
    auth = DatabaseSessionAuth()
    auth._select_workspace = AsyncMock(return_value="fixture-workspace")
    auth._rate_failure = AsyncMock()
    auth._rate_clear = AsyncMock()
    return auth, factory


@pytest.mark.asyncio
async def test_41_logins_leave_pool_free_while_password_jobs_wait(case, monkeypatch):
    auth, factory = case
    release = threading.Event()
    count_lock = threading.Lock()
    counts = {"active": 0, "peak": 0}

    def verify(*_args):
        assert not factory.connection_held.get()
        with count_lock:
            counts["active"] += 1
            counts["peak"] = max(counts["peak"], counts["active"])
        try:
            assert release.wait(3)
            time.sleep(.005)
            return True
        finally:
            with count_lock:
                counts["active"] -= 1

    monkeypatch.setattr(auth, "_verify_password", verify)
    tasks = [asyncio.create_task(auth.login(factory,"fixture-user","fixture-password")) for _ in range(41)]
    try:
        async with asyncio.timeout(2):
            while counts["active"] != 2:
                await asyncio.sleep(.005)
        # An authenticated user/admin read can obtain a connection while login
        # hashing is blocked. This is a resource test, not an RBAC bypass.
        async with asyncio.timeout(.5):
            async with factory.begin():
                assert factory.active == 1
        assert not any(task.done() for task in tasks)
    finally:
        release.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks),5)
    assert len(results) == len(factory.rows) == 41
    assert counts["peak"] == 2
    assert len({token for token,claims in results}) == 41
    assert all(claims.user_id == "fixture-user" for token,claims in results)


@pytest.mark.asyncio
@pytest.mark.parametrize("change",["password","disabled","deleted"])
async def test_credential_changes_during_hash_cannot_issue_session(case, monkeypatch, change):
    auth, factory = case
    async def verify(*_args):
        assert factory.active == 0
        if change == "password":
            factory.user.password_hash = "new-hash"
        elif change == "disabled":
            factory.user.status = "disabled"
        else:
            factory.user.deleted_at = object()
        return True
    monkeypatch.setattr(auth,"_verify_login_password",verify)
    with pytest.raises(AuthenticationError):
        await auth.login(factory,"fixture-user","fixture-password")
    assert not factory.rows
    auth._rate_clear.assert_not_awaited()
    auth._rate_failure.assert_awaited_once()


@pytest.mark.asyncio
async def test_role_change_uses_current_authorization_version_and_scoped_old_token(case, monkeypatch):
    auth, factory = case
    async def verify(*_args):
        assert factory.active == 0
        factory.user.authorization_version = 2
        return True
    monkeypatch.setattr(auth,"_verify_login_password",verify)
    token, claims = await auth.login(factory,"fixture-user","fixture-password",previous_token="old-fixture-token")
    assert claims.authorization_version == 2 and claims.csrf_token and token
    assert len(factory.updates) == 1
    assert auth.token_hash("old-fixture-token") in factory.updates[0].compile().params.values()


@pytest.mark.asyncio
async def test_wrong_password_records_failure_without_creating_session(case, monkeypatch):
    auth, factory = case
    monkeypatch.setattr(auth,"_verify_login_password",AsyncMock(return_value=False))
    with pytest.raises(AuthenticationError):
        await auth.login(factory,"fixture-user","wrong-password")
    assert not factory.rows
    auth._rate_failure.assert_awaited_once()
    auth._rate_clear.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_request_holds_hash_slot_until_thread_finishes(case, monkeypatch):
    auth, factory = case
    auth._password_verifications = asyncio.Semaphore(1)
    release = threading.Event()
    started = threading.Event()
    count = 0
    def verify(*_args):
        nonlocal count
        count += 1
        started.set()
        assert release.wait(3)
        return True
    monkeypatch.setattr(auth,"_verify_password",verify)
    first = asyncio.create_task(auth._verify_login_password("hash","password"))
    try:
        async with asyncio.timeout(2):
            while not started.is_set():
                await asyncio.sleep(.005)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(auth._verify_login_password("hash","password"))
        await asyncio.sleep(.03)
        assert count == 1 and not second.done()
    finally:
        release.set()
    assert await asyncio.wait_for(second,2)


@pytest.mark.asyncio
async def test_hash_exception_releases_slot_and_does_not_issue_session(case, monkeypatch):
    auth, factory = case
    def verify(*_args):
        raise RuntimeError("fixture hash failure")
    monkeypatch.setattr(auth,"_verify_password",verify)
    with pytest.raises(RuntimeError,match="fixture hash failure"):
        await auth.login(factory,"fixture-user","fixture-password")
    await asyncio.sleep(0)
    assert auth._password_verifications._value == 2
    assert not factory.rows
