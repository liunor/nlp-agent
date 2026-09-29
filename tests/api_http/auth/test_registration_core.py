"""Email registration and verification at the real HTTP boundary."""

from __future__ import annotations

import uuid

import httpx
import pytest

from ..support.auth import set_test_auth_code
from ..support.database import MySqlProbe
from ..support.http import json_response, problem_response
from server.user.email import normalize_email


pytestmark = pytest.mark.api_core

CAPTCHA_CODE = "ABCD"
EMAIL_CODE = "123456"


def _unique_email(prefix: str = "user") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}@example.com"


def _captcha(
    client: httpx.Client,
    mysql_probe: MySqlProbe,
    *,
    expired: bool = False,
) -> str:
    response = client.get("/api/v1/auth/captcha")
    payload = json_response(response, 200)
    assert isinstance(payload, dict)
    assert payload["image"].startswith("data:image/png;base64,")
    captcha_id = str(payload["captcha_id"])
    set_test_auth_code(
        mysql_probe,
        kind="captcha",
        subject=captcha_id,
        code=CAPTCHA_CODE,
        expired=expired,
    )
    return captcha_id


def _send_email(
    client: httpx.Client,
    mysql_probe: MySqlProbe,
    email: str,
    *,
    expired_captcha: bool = False,
) -> httpx.Response:
    captcha_id = _captcha(client, mysql_probe, expired=expired_captcha)
    response = client.post(
        "/api/v1/auth/email/send",
        json={
            "email": email,
            "captcha_id": captcha_id,
            "captcha_code": CAPTCHA_CODE,
        },
    )
    if response.status_code == 200:
        set_test_auth_code(
            mysql_probe,
            kind="email",
            subject=normalize_email(email),
            code=EMAIL_CODE,
        )
    return response


def _register(
    client: httpx.Client,
    mysql_probe: MySqlProbe,
    *,
    email: str,
    email_code: str = EMAIL_CODE,
    expired_captcha: bool = False,
) -> httpx.Response:
    captcha_id = _captcha(client, mysql_probe, expired=expired_captcha)
    return client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "email_code": email_code,
            "password": "Phase4-register-password!",
            "display_name": "Phase 4 Registered User",
            "captcha_id": captcha_id,
            "captcha_code": CAPTCHA_CODE,
        },
    )


def test_captcha_is_json_and_single_use(
    http_client: httpx.Client,
    mysql_probe: MySqlProbe,
) -> None:
    captcha_id = _captcha(http_client, mysql_probe)
    request = {
        "email": _unique_email(),
        "captcha_id": captcha_id,
        "captcha_code": CAPTCHA_CODE,
    }

    first = http_client.post("/api/v1/auth/email/send", json=request)
    json_response(first, 200)
    replay = http_client.post("/api/v1/auth/email/send", json=request)
    replay_payload = json_response(replay, 400)
    assert isinstance(replay_payload, dict)
    assert "captcha" in str(replay_payload["detail"]).lower()


def test_captcha_rejects_wrong_and_expired_answers(
    http_client: httpx.Client,
    mysql_probe: MySqlProbe,
) -> None:
    wrong_id = _captcha(http_client, mysql_probe)
    wrong = http_client.post(
        "/api/v1/auth/email/send",
        json={
            "email": _unique_email(),
            "captcha_id": wrong_id,
            "captcha_code": "WRONG",
        },
    )
    assert wrong.status_code == 400

    expired = _send_email(
        http_client,
        mysql_probe,
        _unique_email(),
        expired_captcha=True,
    )
    assert expired.status_code == 400


def test_email_code_lifecycle_and_registration_provision_resources(
    http_client: httpx.Client,
    mysql_probe: MySqlProbe,
    authenticated_client_for,
    api_http_client_factory,
    developer_user,
) -> None:
    email = _unique_email()
    sent = _send_email(http_client, mysql_probe, email)
    json_response(sent, 200)

    registered = _register(http_client, mysql_probe, email=email)
    payload = json_response(registered, 201)
    assert isinstance(payload, dict)
    user_id = str(payload["user_id"])
    assert payload["username"].startswith("user")

    developer_client = authenticated_client_for(developer_user)
    user = json_response(
        developer_client.get(f"/api/v1/users/{user_id}"),
        200,
    )
    assert isinstance(user, dict)
    assert user["roles"] == ["guest"]
    assert user["status"] == "active"

    registered_login = api_http_client_factory(
        base_url=http_client.base_url,
        timeout=15,
    )
    # Login accepts the verified email as the identity credential.
    response = registered_login.post(
        "/api/v1/auth/login",
        headers={"Origin": str(http_client.base_url).rstrip("/")},
        json={
            "username": email,
            "password": "Phase4-register-password!",
        },
    )
    login_payload = json_response(response, 200)
    assert isinstance(login_payload, dict)
    assert login_payload["user_id"] == user_id
    assert login_payload["workspace_ids"]


def test_email_wrong_expired_and_replayed_codes_are_rejected(
    http_client: httpx.Client,
    mysql_probe: MySqlProbe,
) -> None:
    wrong_email = _unique_email()
    assert _send_email(http_client, mysql_probe, wrong_email).status_code == 200
    wrong = _register(http_client, mysql_probe, email=wrong_email, email_code="000000")
    assert wrong.status_code == 400

    expired_email = _unique_email()
    assert _send_email(http_client, mysql_probe, expired_email).status_code == 200
    set_test_auth_code(
        mysql_probe,
        kind="email",
        subject=normalize_email(expired_email),
        code=EMAIL_CODE,
        expired=True,
    )
    expired = _register(http_client, mysql_probe, email=expired_email)
    assert expired.status_code == 400

    replay_email = _unique_email()
    assert _send_email(http_client, mysql_probe, replay_email).status_code == 200
    first = _register(http_client, mysql_probe, email=replay_email)
    assert first.status_code == 201, first.text
    replay = _register(http_client, mysql_probe, email=replay_email)
    assert replay.status_code == 400


def test_duplicate_email_is_rejected_after_fresh_verification(
    http_client: httpx.Client,
    mysql_probe: MySqlProbe,
) -> None:
    email = _unique_email()
    assert _send_email(http_client, mysql_probe, email).status_code == 200
    first = _register(http_client, mysql_probe, email=email)
    assert first.status_code == 201, first.text

    set_test_auth_code(
        mysql_probe,
        kind="email",
        subject=normalize_email(email),
        code=EMAIL_CODE,
    )
    duplicate = _register(http_client, mysql_probe, email=email)
    assert duplicate.status_code == 409


def test_email_resend_rate_limit_is_enforced(
    http_client: httpx.Client,
    mysql_probe: MySqlProbe,
) -> None:
    email = _unique_email()
    first = _send_email(http_client, mysql_probe, email)
    assert first.status_code == 200, first.text
    second = _send_email(http_client, mysql_probe, email)
    assert second.status_code == 429, second.text


def test_email_provider_failure_has_explicit_gateway_error(
    http_client: httpx.Client,
    mysql_probe: MySqlProbe,
) -> None:
    # The deterministic stub fails delivery for any address with this prefix.
    email = f"fail{uuid.uuid4().hex[:10]}@example.com"
    captcha_id = _captcha(http_client, mysql_probe)
    response = http_client.post(
        "/api/v1/auth/email/send",
        json={
            "email": email,
            "captcha_id": captcha_id,
            "captcha_code": CAPTCHA_CODE,
        },
    )
    payload = json_response(response, 502)
    assert isinstance(payload, dict)
    assert "Email gateway failed" in str(payload["detail"])
    assert (
        mysql_probe.scalar(
            "SELECT COUNT(*) FROM nlp_auth_codes "
            "WHERE kind='email' AND subject=:subject",
            subject=normalize_email(email),
        )
        == 0
    )
