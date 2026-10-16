from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import httpx
import pytest

from server.web.auth import AuthenticationError, LoginRateLimitError, SameOriginSessionAuth
from server.web.database_auth import DatabaseSessionAuth, _RateLimiter


@pytest.mark.asyncio
async def test_other_account_on_school_ip_survives_five_distinct_failures():
    auth = DatabaseSessionAuth(max_login_attempts=5, max_ip_login_attempts=100, rate_window_s=900)
    for i in range(5):
        await auth._rate_failure(f"learner-{i}", "198.51.100.20")
    assert await auth._rate_allowed("another-learner", "198.51.100.20")


@pytest.mark.asyncio
async def test_username_limit_still_applies_across_different_ips():
    auth = DatabaseSessionAuth(max_login_attempts=5, max_ip_login_attempts=100, rate_window_s=900)
    for i in range(5):
        await auth._rate_failure("learner", f"198.51.100.{i+1}")
    assert not await auth._rate_allowed("learner", "203.0.113.8")
    assert await auth._rate_allowed("different-learner", "203.0.113.8")


@pytest.mark.asyncio
async def test_ip_budget_still_bounds_many_account_failures():
    auth = DatabaseSessionAuth(max_login_attempts=5, max_ip_login_attempts=7, rate_window_s=900)
    for i in range(7):
        await auth._rate_failure(f"learner-{i}", "198.51.100.20")
    assert not await auth._rate_allowed("new-account", "198.51.100.20")
    assert await auth._rate_allowed("new-account", "203.0.113.8")


@pytest.mark.asyncio
async def test_success_clears_only_its_username_and_keeps_shared_ip_budget():
    auth = DatabaseSessionAuth(max_login_attempts=2, max_ip_login_attempts=4)
    await auth._rate_failure("alice", "198.51.100.20")
    await auth._rate_failure("alice", "198.51.100.20")
    await auth._rate_clear("alice", "198.51.100.20")
    assert await auth._rate_allowed("alice", "198.51.100.20")
    await auth._rate_failure("bob", "198.51.100.20")
    await auth._rate_failure("carol", "198.51.100.20")
    assert not await auth._rate_allowed("different-account", "198.51.100.20")


def test_retry_after_expires_and_handles_overlapping_attempts(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("server.web.database_auth.monotonic", lambda: now[0])
    limiter = _RateLimiter(2, 10)
    limiter.record_failure("key")
    now[0] = 2.0
    limiter.record_failure("key")
    now[0] = 3.0
    limiter.record_failure("key")
    assert limiter.retry_after("key") == 9
    now[0] = 10.0
    assert limiter.retry_after("key") == 2
    now[0] = 12.0
    assert limiter.allowed("key")


@pytest.mark.asyncio
async def test_username_and_ip_windows_are_independent(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("server.web.database_auth.monotonic", lambda: now[0])
    auth = DatabaseSessionAuth(max_login_attempts=2, max_ip_login_attempts=2,
                               rate_window_s=10, ip_rate_window_s=20)
    await auth._rate_failure("alice", "198.51.100.20")
    await auth._rate_failure("alice", "198.51.100.20")
    now[0] = 11.0
    assert await auth._rate_allowed("alice", "203.0.113.8")
    assert await auth._rate_retry_after("new-account", "198.51.100.20") == 9
    now[0] = 20.0
    assert await auth._rate_allowed("alice", "198.51.100.20")


def test_environment_can_configure_ip_limit_without_changing_account_limit(monkeypatch):
    monkeypatch.setenv("NLP_AGENT_AUTH_MAX_LOGIN_ATTEMPTS", "5")
    monkeypatch.setenv("NLP_AGENT_AUTH_RATE_WINDOW_S", "900")
    monkeypatch.setenv("NLP_AGENT_AUTH_MAX_IP_LOGIN_ATTEMPTS", "120")
    monkeypatch.setenv("NLP_AGENT_AUTH_IP_RATE_WINDOW_S", "600")
    auth = DatabaseSessionAuth.from_config({})
    assert (auth._username_rate_limiter.max_attempts, auth._username_rate_limiter.window_s) == (5, 900)
    assert (auth._client_rate_limiter.max_attempts, auth._client_rate_limiter.window_s) == (120, 600)


@pytest.mark.asyncio
async def test_limited_login_stops_before_any_database_operation():
    auth = DatabaseSessionAuth(max_login_attempts=1)
    await auth._rate_failure("learner", "198.51.100.20")
    with pytest.raises(LoginRateLimitError) as exc:
        await auth.login(None, "LEARNER", "irrelevant", client_key="198.51.100.20")
    assert 1 <= exc.value.retry_after_s <= 300
    assert isinstance(exc.value, AuthenticationError)


@pytest.mark.asyncio
async def test_api_rate_limit_is_429_with_retry_information():
    from server.web.app import create_app

    class LimitedAuth(SameOriginSessionAuth):
        def login(self, *args, **kwargs):
            raise LoginRateLimitError(42)

    app = create_app(auth=LimitedAuth(secret="only-for-offline-tests", allowed_origins=["http://testserver"]),
                     allowed_hosts=["testserver"])
    app.state.gateway = SimpleNamespace(authorization_session_factory=None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post("/api/v1/auth/login", json={"username": "learner", "password": "irrelevant"},
                                     headers={"Origin": "http://testserver"})
    assert response.status_code == 429
    assert response.json()["code"] == "login_rate_limited"
    assert response.json()["retry_after_s"] == 42
    assert response.headers["Retry-After"] == "42"
    assert "set-cookie" not in response.headers


@pytest.mark.asyncio
async def test_real_redis_distinct_limits_ttl_and_success_behavior():
    """Set TEST_AUTH_REDIS_URL only for an isolated rehearsal Redis instance."""
    url = os.environ.get("TEST_AUTH_REDIS_URL")
    if not url:
        pytest.skip("isolated Redis URL was not supplied")
    from redis.asyncio import Redis

    redis = Redis.from_url(url)
    auth = DatabaseSessionAuth(max_login_attempts=2, max_ip_login_attempts=4,
                               rate_window_s=60, ip_rate_window_s=120)
    auth._rate_prefix = f"auth-ip-rehearsal:{uuid.uuid4().hex}:"
    auth.set_redis_client(redis)
    keys = set()
    try:
        for name in ["alice", "alice", "bob", "carol"]:
            keys.update(auth._rate_keys(name, "198.51.100.20"))
        await auth._rate_failure("alice", "198.51.100.20")
        await auth._rate_failure("alice", "198.51.100.20")
        assert not await auth._rate_allowed("alice", "203.0.113.8")
        assert await auth._rate_allowed("bob", "198.51.100.20")
        user_key, ip_key = auth._rate_keys("alice", "198.51.100.20")
        assert 1 <= await redis.ttl(user_key) <= 60
        assert 61 <= await redis.ttl(ip_key) <= 120
        await auth._rate_clear("alice", "198.51.100.20")
        assert await redis.get(user_key) is None
        assert int(await redis.get(ip_key)) == 2
        await auth._rate_failure("bob", "198.51.100.20")
        await auth._rate_failure("carol", "198.51.100.20")
        assert not await auth._rate_allowed("new-account", "198.51.100.20")
        assert await auth._rate_allowed("new-account", "203.0.113.8")
        await redis.persist(ip_key)
        assert await redis.ttl(ip_key) == -1
        assert 1 <= await auth._rate_retry_after("new-account", "198.51.100.20") <= 120
        assert await redis.ttl(ip_key) > 0
    finally:
        await redis.delete(*keys)
        await redis.aclose()
