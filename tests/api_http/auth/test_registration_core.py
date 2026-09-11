"""Phone registration and verification at the real HTTP boundary."""

from __future__ import annotations

import uuid

import httpx
import pytest

from ..support.auth import set_test_auth_code
from ..support.database import MySqlProbe
from ..support.http import json_response, problem_response
from server.user.phone import normalize_phone_number


pytestmark = pytest.mark.api_core

CAPTCHA_CODE = "ABCD"
SMS_CODE = "123456"


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


def _send_sms(
    client: httpx.Client,
    mysql_probe: MySqlProbe,
    phone: str,
    *,
    expired_captcha: bool = False,
) -> httpx.Response:
    captcha_id = _captcha(client, mysql_probe, expired=expired_captcha)
    response = client.post(
        "/api/v1/auth/sms/send",
        json={
            "phone_number": phone,
            "captcha_id": captcha_id,
            "captcha_code": CAPTCHA_CODE,
        },
    )
    if response.status_code == 200:
        set_test_auth_code(
            mysql_probe,
            kind="sms",
            subject=normalize_phone_number(phone),
            code=SMS_CODE,
        )
    return response


def _register(
    client: httpx.Client,
    mysql_probe: MySqlProbe,
    *,
    phone: str,
    sms_code: str = SMS_CODE,
    expired_captcha: bool = False,
) -> httpx.Response:
    captcha_id = _captcha(client, mysql_probe, expired=expired_captcha)
    return client.post(
        "/api/v1/auth/register",
        json={
            "phone_number": phone,
            "sms_code": sms_code,
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
        "phone_number": f"138{uuid.uuid4().int % 10**8:08d}",
        "captcha_id": captcha_id,
        "captcha_code": CAPTCHA_CODE,
    }

    first = http_client.post("/api/v1/auth/sms/send", json=request)
    json_response(first, 200)
    replay = http_client.post("/api/v1/auth/sms/send", json=request)
    replay_payload = json_response(replay, 400)
    assert isinstance(replay_payload, dict)
    assert "captcha" in str(replay_payload["detail"]).lower()


def test_captcha_rejects_wrong_and_expired_answers(
    http_client: httpx.Client,
    mysql_probe: MySqlProbe,
) -> None:
    wrong_id = _captcha(http_client, mysql_probe)
    wrong = http_client.post(
        "/api/v1/auth/sms/send",
        json={
            "phone_number": "138" + str(uuid.uuid4().int % 10**8).zfill(8),
            "captcha_id": wrong_id,
            "captcha_code": "WRONG",
        },
    )
    assert wrong.status_code == 400

    expired = _send_sms(
        http_client,
        mysql_probe,
        "139" + str(uuid.uuid4().int % 10**8).zfill(8),
        expired_captcha=True,
    )
    assert expired.status_code == 400


def test_sms_code_lifecycle_and_registration_provision_resources(
    http_client: httpx.Client,
    mysql_probe: MySqlProbe,
    authenticated_client_for,
    api_http_client_factory,
    developer_user,
) -> None:
    phone = "138" + str(uuid.uuid4().int % 10**8).zfill(8)
    sent = _send_sms(http_client, mysql_probe, phone)
    json_response(sent, 200)

    registered = _register(http_client, mysql_probe, phone=phone)
    payload = json_response(registered, 201)
    assert isinstance(payload, dict)
    user_id = str(payload["user_id"])
    assert payload["username"] == normalize_phone_number(phone)[1:]

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
    response = registered_login.post(
        "/api/v1/auth/login",
        headers={"Origin": str(http_client.base_url).rstrip("/")},
        json={
            "username": payload["username"],
            "password": "Phase4-register-password!",
        },
    )
    login_payload = json_response(response, 200)
    assert isinstance(login_payload, dict)
    assert login_payload["user_id"] == user_id
    assert login_payload["workspace_ids"]


def test_sms_wrong_expired_and_replayed_codes_are_rejected(
    http_client: httpx.Client,
    mysql_probe: MySqlProbe,
) -> None:
    wrong_phone = "138" + str(uuid.uuid4().int % 10**8).zfill(8)
    assert _send_sms(http_client, mysql_probe, wrong_phone).status_code == 200
    wrong = _register(http_client, mysql_probe, phone=wrong_phone, sms_code="000000")
    assert wrong.status_code == 400

    expired_phone = "139" + str(uuid.uuid4().int % 10**8).zfill(8)
    assert _send_sms(http_client, mysql_probe, expired_phone).status_code == 200
    set_test_auth_code(
        mysql_probe,
        kind="sms",
        subject=normalize_phone_number(expired_phone),
        code=SMS_CODE,
        expired=True,
    )
    expired = _register(http_client, mysql_probe, phone=expired_phone)
    assert expired.status_code == 400

    replay_phone = "137" + str(uuid.uuid4().int % 10**8).zfill(8)
    assert _send_sms(http_client, mysql_probe, replay_phone).status_code == 200
    first = _register(http_client, mysql_probe, phone=replay_phone)
    assert first.status_code == 201, first.text
    replay = _register(http_client, mysql_probe, phone=replay_phone)
    assert replay.status_code == 400


def test_duplicate_phone_is_rejected_after_fresh_verification(
    http_client: httpx.Client,
    mysql_probe: MySqlProbe,
) -> None:
    phone = "136" + str(uuid.uuid4().int % 10**8).zfill(8)
    assert _send_sms(http_client, mysql_probe, phone).status_code == 200
    first = _register(http_client, mysql_probe, phone=phone)
    assert first.status_code == 201, first.text

    set_test_auth_code(
        mysql_probe,
        kind="sms",
        subject=normalize_phone_number(phone),
        code=SMS_CODE,
    )
    duplicate = _register(http_client, mysql_probe, phone=phone)
    assert duplicate.status_code == 409


def test_sms_resend_rate_limit_is_enforced(
    http_client: httpx.Client,
    mysql_probe: MySqlProbe,
) -> None:
    phone = "135" + str(uuid.uuid4().int % 10**8).zfill(8)
    first = _send_sms(http_client, mysql_probe, phone)
    assert first.status_code == 200, first.text
    second = _send_sms(http_client, mysql_probe, phone)
    assert second.status_code == 429, second.text


def test_sms_provider_failure_has_explicit_gateway_error(
    http_client: httpx.Client,
    mysql_probe: MySqlProbe,
) -> None:
    phone = "131" + str(uuid.uuid4().int % 10**8).zfill(8)
    captcha_id = _captcha(http_client, mysql_probe)
    response = http_client.post(
        "/api/v1/auth/sms/send",
        json={
            "phone_number": phone,
            "captcha_id": captcha_id,
            "captcha_code": CAPTCHA_CODE,
        },
    )
    payload = json_response(response, 502)
    assert isinstance(payload, dict)
    assert "SMS gateway failed" in str(payload["detail"])
    assert (
        mysql_probe.scalar(
            "SELECT COUNT(*) FROM nlp_auth_codes "
            "WHERE kind='sms' AND subject=:subject",
            subject=normalize_phone_number(phone),
        )
        == 0
    )
