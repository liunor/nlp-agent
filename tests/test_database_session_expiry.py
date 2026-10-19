"""HTTP and established WebSocket sessions must enforce one expiry policy."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from server.web import database_auth
from server.web.auth import AuthenticationError, AuthorizationChangedError

NOW = datetime(2026, 10, 19, 10, 0)


class Factory:
    def __init__(self, row):
        self.row = row
        self.committed = False
        self.rolled_back = False

    @asynccontextmanager
    async def begin(self):
        try:
            yield self
        except BaseException:
            self.rolled_back = True
            raise
        else:
            self.committed = True

    async def scalar(self, statement):
        assert statement.is_select
        return self.row


@pytest.fixture
def case(monkeypatch):
    row = SimpleNamespace(id="session", user_id="user", workspace_id="workspace",
        token_hash="opaque-hash", csrf_hash="csrf-hash", authorization_version=1,
        issued_at=NOW-timedelta(minutes=5), expires_at=NOW+timedelta(minutes=20),
        last_seen_at=NOW-timedelta(seconds=59), revoked_at=None,
        user=SimpleNamespace(status="active", deleted_at=None, authorization_version=1))
    release = AsyncMock()
    monkeypatch.setattr(database_auth, "_utc_now", lambda: NOW)
    monkeypatch.setattr(database_auth.sandbox_lifecycle_service,
                        "release_auth_session_in_transaction", release)
    return SimpleNamespace(row=row, factory=Factory(row), release=release,
                           auth=database_auth.DatabaseSessionAuth(ttl_s=3600, idle_timeout_s=60))


async def authenticate(case, path, touch):
    if path == "http":
        return await case.auth.authenticate(case.factory, "SyntheticToken", touch=touch)
    return await case.auth.authenticate_session_id(case.factory, "session", touch=touch)


@pytest.mark.parametrize("path", ["http", "websocket"])
@pytest.mark.parametrize("touch", [False, True])
async def test_idle_expiry_commits_revocation_and_cleanup_before_error(case, path, touch):
    before = NOW-timedelta(seconds=61)
    case.row.last_seen_at = before
    with pytest.raises(AuthenticationError, match="expired"):
        await authenticate(case, path, touch)
    assert case.factory.committed and not case.factory.rolled_back
    assert case.row.revoked_at == NOW
    assert case.row.last_seen_at == before
    case.release.assert_awaited_once_with(case.factory, user_id="user", auth_session_id="session",
                                          reason="auth.session.expired")


@pytest.mark.parametrize("path", ["http", "websocket"])
@pytest.mark.parametrize("touch", [False, True])
async def test_active_boundary_and_activity_preserve_absolute_cap(case, path, touch):
    before = NOW-timedelta(seconds=60)
    expiry = case.row.expires_at
    case.row.last_seen_at = before
    claims = await authenticate(case, path, touch)
    assert claims.user_id == "user"
    assert case.row.last_seen_at == (NOW if touch else before)
    assert case.row.revoked_at is None
    assert case.row.expires_at <= case.row.issued_at+timedelta(seconds=case.auth.ttl_s)
    if path == "websocket" or not touch:
        assert case.row.expires_at == expiry
    case.release.assert_not_awaited()


@pytest.mark.parametrize("path", ["http", "websocket"])
@pytest.mark.parametrize("invalid", ["absolute", "revoked", "disabled", "deleted", "authorization"])
async def test_other_security_fences_remain_effective(case, path, invalid):
    if invalid == "absolute":
        case.row.expires_at = NOW
    elif invalid == "revoked":
        case.row.revoked_at = NOW-timedelta(seconds=1)
    elif invalid == "disabled":
        case.row.user.status = "disabled"
    elif invalid == "deleted":
        case.row.user.deleted_at = NOW-timedelta(seconds=1)
    else:
        case.row.user.authorization_version = 2
    expected = AuthorizationChangedError if invalid == "authorization" else AuthenticationError
    with pytest.raises(expected):
        await authenticate(case, path, True)
    assert case.factory.committed
    assert case.row.last_seen_at < NOW
    case.release.assert_awaited_once()


@pytest.mark.parametrize("path", ["http", "websocket"])
async def test_failed_cleanup_rolls_back_and_does_not_issue_claims(case, path):
    case.row.last_seen_at = NOW-timedelta(seconds=61)
    case.release.side_effect = RuntimeError("synthetic database failure")
    with pytest.raises(RuntimeError, match="synthetic database failure"):
        await authenticate(case, path, True)
    assert case.factory.rolled_back and not case.factory.committed


@pytest.mark.parametrize("path", ["http", "websocket"])
async def test_missing_row_cannot_authenticate_or_release_another_session(case, path):
    case.factory.row = None
    with pytest.raises(AuthenticationError):
        await authenticate(case, path, False)
    case.release.assert_not_awaited()
