"""Quota credit targets use public identities only to resolve stable user IDs."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.quota.credit_identity import resolve_credit_user_id


@pytest.mark.asyncio
async def test_credit_identity_resolves_only_active_users_and_retains_internal_id():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE nlp_users (id TEXT PRIMARY KEY, identity_id TEXT UNIQUE, status TEXT, deleted_at TEXT)"))
        await conn.execute(text("INSERT INTO nlp_users VALUES ('user-1', 'TEACHER01', 'active', NULL), ('user-2', 'STUDENT01', 'disabled', NULL), ('user-3', 'REMOVED01', 'active', '2026-01-01')"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        assert await resolve_credit_user_id(factory, " teacher01 ") == "user-1"
        assert await resolve_credit_user_id(factory, "user-1") == "user-1"
        assert await resolve_credit_user_id(factory, "student01") is None
        assert await resolve_credit_user_id(factory, "removed01") is None
        assert await resolve_credit_user_id(factory, "missing01") is None
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE nlp_users SET identity_id = 'NEWID01' WHERE id = 'user-1'"))
        assert await resolve_credit_user_id(factory, "TEACHER01") is None
        assert await resolve_credit_user_id(factory, "NEWID01") == "user-1"
        assert await resolve_credit_user_id(factory, "user-1") == "user-1"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("operation", ["gift", "reset"])
def test_credit_http_uses_resolved_user_id_for_accounting_and_notification(monkeypatch, operation):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi.testclient import TestClient

    from core.identity import AuthenticatedPrincipal
    import server.web.app as web
    from server.web.auth import SameOriginSessionAuth

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def setup():
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE nlp_users (id TEXT PRIMARY KEY, identity_id TEXT UNIQUE, status TEXT, deleted_at TEXT)"))
            await conn.execute(text("INSERT INTO nlp_users VALUES ('user-1', 'TEACHER01', 'active', NULL)"))

    asyncio.run(setup())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    calls = []

    class FakeOperations:
        def gift_credits(self, management, **kwargs):
            calls.append(kwargs)
            return {"operation_id": "op-1", "owner_type": kwargs["owner_type"], "owner_id": kwargs["owner_id"], "amount_micro": kwargs["amount_micro"]}

        reset_credits = gift_credits

    monkeypatch.setattr(web.rbac_service, "audit", AsyncMock())
    monkeypatch.setattr(web.rbac_service, "principal_for_username", AsyncMock(return_value=AuthenticatedPrincipal.system_admin()))
    auth = SameOriginSessionAuth(secret="test-secret-that-is-long-enough", allowed_origins=["http://testserver"])
    app = web.create_app(auth=auth, allowed_hosts=["testserver"])
    app.state.gateway = SimpleNamespace(authorization_session_factory=factory)
    app.state.quota_operations = FakeOperations()
    app.state.quota_management = object()
    publisher = SimpleNamespace(publish=AsyncMock())
    app.state.quota_snapshot_redis = publisher
    token, claims = auth.issue(AuthenticatedPrincipal.system_admin())
    client = TestClient(app)
    client.cookies.set(auth.cookie_name, token)
    body = {"owner_type": "user", "owner_id": " teacher01 ", "bucket_type": "daily",
            "period_start": "2026-11-04T00:00:00Z", "period_end": "2026-11-05T00:00:00Z",
            "amount_micro": 1000000, "reason": "test", "idempotency_key": "test-key",
            "effective_from": "2026-11-04T00:00:00Z"}
    headers = {"Origin": "http://testserver", "X-CSRF-Token": claims.csrf_token}
    try:
        response = client.post(f"/api/v1/developer/quota/credits/{operation}", json=body, headers=headers)
        assert response.status_code == 201, response.text
        assert calls[0]["owner_id"] == "user-1"
        assert response.json()["owner_id"] == "user-1"
        assert '"owner_id":"user-1"' in publisher.publish.await_args.args[1]
        missing = client.post(f"/api/v1/developer/quota/credits/{operation}",
                              json={**body, "owner_id": "MISSING01"}, headers=headers)
        assert missing.status_code == 422
        assert missing.json()["code"] == "quota_subject_not_found"
        assert len(calls) == 1
        student = AuthenticatedPrincipal(user_id="student-1", roles=frozenset({"student"}))
        web.rbac_service.principal_for_username.return_value = student
        student_token, student_claims = auth.issue(student)
        other = TestClient(app)
        try:
            other.cookies.set(auth.cookie_name, student_token)
            denied = other.post(f"/api/v1/developer/quota/credits/{operation}", json=body,
                                headers={"Origin": "http://testserver", "X-CSRF-Token": student_claims.csrf_token})
            assert denied.status_code == 403
            assert len(calls) == 1
        finally:
            other.close()
    finally:
        client.close()
        asyncio.run(engine.dispose())
