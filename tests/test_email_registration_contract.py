"""Public email-only HTTP contracts; no real mail, SMS or external database."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from server.auth import code_store
from server.auth.dependencies import get_db_session
from server.web import app as web


@pytest.fixture
def registration_client():
    app = web.create_app(allowed_hosts=["testserver"])
    app.state.gateway = SimpleNamespace(authorization_session_factory=None)

    async def isolated_db():
        yield object()

    app.dependency_overrides[get_db_session] = isolated_db
    # Do not enter the lifecycle: no gateway, real DB, worker or provider starts.
    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()


@pytest.mark.parametrize("body", [
    {"phone_number": "+8613800138000", "sms_code": "123456"},
    {"email": "fixture@example.com", "email_code": "123456", "sms_code": "123456"},
    {"email": "fixture@example.com"},
])
def test_public_registration_rejects_phone_sms_or_missing_email_code(registration_client, monkeypatch, body):
    register = AsyncMock()
    monkeypatch.setattr(web.UserService, "register_user", register)
    response = registration_client.post("/api/v1/auth/register", json={
        **body, "password": "fixture-password", "captcha_id": "fixture", "captcha_code": "ABCD",
    })
    assert response.status_code == 422
    register.assert_not_awaited()


@pytest.mark.parametrize("body", [{}, {"phone_number": "+8613800138000", "captcha_id": "fixture", "captcha_code": "ABCD"}])
def test_sms_is_disabled_without_consuming_codes_or_using_a_provider(registration_client, monkeypatch, body):
    consume = AsyncMock()
    monkeypatch.setattr(code_store, "consume_code", consume)
    response = registration_client.post("/api/v1/auth/sms/send", json=body)
    assert response.status_code == 410
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "sms_registration_disabled"
    consume.assert_not_awaited()


def test_public_registration_schema_requires_email_and_does_not_advertise_phone(registration_client):
    schema = registration_client.app.openapi()
    registration = schema["components"]["schemas"]["EmailUserRegister"]
    assert {"email", "email_code"} <= set(registration["required"])
    assert "phone_number" not in registration["properties"]
    assert "sms_code" not in registration["properties"]
    assert registration["additionalProperties"] is False


def test_email_registration_returns_guest_without_a_login_cookie(registration_client, monkeypatch):
    register = AsyncMock(return_value=SimpleNamespace(
        id="fixture-user", username="userfixture", display_name="同学",
    ))
    monkeypatch.setattr(web.UserService, "register_user", register)
    monkeypatch.setattr(web.UserService, "get_roles_for_users", AsyncMock(return_value={"fixture-user": ["guest"]}))
    response = registration_client.post("/api/v1/auth/register", json={
        "email": "fixture@example.com", "email_code": "123456", "display_name": "同学",
        "password": "fixture-password", "captcha_id": "fixture", "captcha_code": "ABCD",
    })
    assert response.status_code == 201
    assert response.json()["roles"] == ["guest"]
    assert "set-cookie" not in response.headers
    data = register.await_args.args[0]
    assert data.email == "fixture@example.com"
    assert data.email_code == "123456"
    assert data.phone_number is None
    assert data.sms_code is None


@pytest.mark.parametrize("delivered", [True, False])
def test_email_response_contract_and_failure_does_not_store_a_code(registration_client, monkeypatch, delivered):
    @asynccontextmanager
    async def isolated_lock(*_args):
        yield

    provider = SimpleNamespace(send_verification_code=AsyncMock(return_value=delivered))
    monkeypatch.setattr(web, "create_email_provider_from_env", lambda: provider)
    monkeypatch.setattr(code_store, "email_send_lock", isolated_lock)
    monkeypatch.setattr(code_store, "purge_expired", AsyncMock())
    monkeypatch.setattr(code_store, "consume_code", AsyncMock(return_value=True))
    monkeypatch.setattr(code_store, "email_send_allowed", AsyncMock(return_value=(True, "")))
    record = AsyncMock()
    store = AsyncMock()
    monkeypatch.setattr(code_store, "record_email_send", record)
    monkeypatch.setattr(code_store, "put_code", store)
    response = registration_client.post("/api/v1/auth/email/send", json={
        "email": "fixture@example.com", "captcha_id": "fixture", "captcha_code": "ABCD",
    })
    if delivered:
        assert response.status_code == 200
        assert response.json()["expires_in"] == 300
        assert response.json()["resend_after"] == 60
        assert store.await_args.kwargs["kind"] == "email"
        assert store.await_args.kwargs["ttl_s"] == 300
        assert record.await_args.kwargs["outcome"] == "sent"
    else:
        assert response.status_code == 502
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["code"] == "email_gateway_failed"
        assert response.json()["request_id"] == response.headers["x-request-id"]
        assert record.await_args.kwargs["outcome"] == "failed"
        store.assert_not_awaited()
    provider.send_verification_code.assert_awaited_once()
