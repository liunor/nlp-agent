from __future__ import annotations

import time

import pytest
from argon2 import PasswordHasher

from core.identity import AuthenticatedPrincipal
from server.web.auth import AuthenticationError, SameOriginSessionAuth


def make_auth(**overrides) -> SameOriginSessionAuth:
    config = {
        "secret": "test-secret-that-is-long-enough-for-hmac",
        "username": "nova",
        "password_hash": PasswordHasher().hash("correct-password"),
        "idle_timeout_s": 60,
        "max_login_attempts": 3,
        "rate_window_s": 300,
    }
    config.update(overrides)
    return SameOriginSessionAuth(**config)


def test_login_issues_stateful_universal_session_and_authenticates_it() -> None:
    auth = make_auth()

    token, claims = auth.login("nova", "correct-password")

    assert claims.principal() == AuthenticatedPrincipal(
        user_id="nova",
        workspace_ids=frozenset({"default"}),
        roles=frozenset({"student", "teacher", "admin"}),
    )
    assert auth.authenticate(token) == claims
    assert "." not in token


def test_invalid_credentials_are_rejected_without_issuing_a_session() -> None:
    auth = make_auth()

    with pytest.raises(AuthenticationError, match="invalid credentials"):
        auth.login("nova", "wrong-password")

    with pytest.raises(AuthenticationError):
        auth.authenticate(None)


def test_missing_fixed_credentials_fails_closed() -> None:
    auth = SameOriginSessionAuth(secret="test-secret-that-is-long-enough-for-hmac")

    with pytest.raises(AuthenticationError, match="not configured"):
        auth.login("nova", "correct-password")


def test_logout_revokes_the_session_immediately() -> None:
    auth = make_auth()
    token, _claims = auth.login("nova", "correct-password")

    auth.revoke(token)

    with pytest.raises(AuthenticationError, match="invalid"):
        auth.authenticate(token)


def test_each_login_rotates_the_session_token() -> None:
    auth = make_auth()

    first, _claims = auth.login("nova", "correct-password")
    second, _claims = auth.login("nova", "correct-password", previous_token=first)

    assert first != second
    with pytest.raises(AuthenticationError, match="invalid"):
        auth.authenticate(first)


def test_login_rate_limit_blocks_repeated_failures() -> None:
    auth = make_auth(max_login_attempts=2)

    for _ in range(2):
        with pytest.raises(AuthenticationError, match="invalid credentials"):
            auth.login("nova", "wrong-password")

    with pytest.raises(AuthenticationError, match="too many login attempts"):
        auth.login("nova", "correct-password")


def test_idle_timeout_expires_session() -> None:
    auth = make_auth(idle_timeout_s=60)
    token, _claims = auth.login("nova", "correct-password")
    session = next(iter(auth._sessions.values()))
    session.last_seen_at = time.monotonic() - 61

    with pytest.raises(AuthenticationError, match="expired"):
        auth.authenticate(token)


def test_read_only_session_check_does_not_extend_idle_timeout() -> None:
    auth = make_auth()
    token, _claims = auth.login("nova", "correct-password")
    session = next(iter(auth._sessions.values()))
    last_seen_at = session.last_seen_at

    assert auth.authenticate(token, touch=False).user_id == "nova"
    assert session.last_seen_at == last_seen_at


def test_stateless_monitor_compatibility_remains_available_without_credentials() -> None:
    auth = SameOriginSessionAuth(secret="monitor-test-secret")
    token, claims = auth.issue(
        AuthenticatedPrincipal(
            user_id="local",
            workspace_ids=frozenset({"default"}),
            roles=frozenset({"admin"}),
        )
    )

    assert auth.authenticate(token) == claims
    assert "." in token


def test_login_issues_expiry_from_ttl() -> None:
    auth = make_auth(ttl_s=300)
    _token, claims = auth.login("nova", "correct-password")

    now = int(time.time())
    assert now + 299 <= claims.expires_at <= now + 301


def test_touch_extends_existing_session_with_current_ttl() -> None:
    auth = make_auth(ttl_s=300)
    token, claims = auth.login("nova", "correct-password")
    original_expiry = claims.expires_at

    # Simulate a TTL increase after the session was already issued.
    auth.ttl_s = 900
    refreshed = auth.authenticate(token)

    assert refreshed.expires_at >= original_expiry + 600
    assert refreshed.expires_at <= int(time.time()) + 901
