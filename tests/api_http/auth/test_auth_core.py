"""Core authentication behavior at the real HTTP boundary."""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from ..support.auth import LoginResult, same_origin_headers
from ..support.environment import SeededUser
from ..support.resources import create_user


pytestmark = pytest.mark.api_core


def test_wrong_password_is_rejected(
    http_client: httpx.Client,
    api_http_environment,
    student_user: SeededUser,
) -> None:
    response = http_client.post(
        "/api/v1/auth/login",
        headers=same_origin_headers(api_http_environment.web_origin),
        json={"username": student_user.username, "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


def test_unknown_user_is_rejected(
    http_client: httpx.Client,
    api_http_environment,
) -> None:
    response = http_client.post(
        "/api/v1/auth/login",
        headers=same_origin_headers(api_http_environment.web_origin),
        json={"username": "phase3unknownuser", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


def test_logout_revokes_the_database_session(
    authenticated_client: httpx.Client,
) -> None:
    response = authenticated_client.delete("/api/v1/auth/session")
    assert response.status_code == 204
    assert "nlp_session" not in authenticated_client.cookies

    session = authenticated_client.get("/api/v1/auth/session")
    assert session.status_code == 401


def test_protected_api_rejects_missing_cookie(http_client: httpx.Client) -> None:
    response = http_client.get("/api/v1/users/me")
    assert response.status_code == 401


def test_protected_api_rejects_invalid_cookie(http_client: httpx.Client) -> None:
    http_client.cookies.set("nlp_session", "invalid-session-token")
    response = http_client.get("/api/v1/users/me")
    assert response.status_code == 401


def test_write_rejects_missing_csrf(
    real_login: LoginResult,
    http_client: httpx.Client,
    api_http_environment,
) -> None:
    del real_login
    response = http_client.post(
        "/api/v1/sessions",
        headers=same_origin_headers(api_http_environment.web_origin),
        json={"workspace_id": "not-used"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_rejected"


def test_write_rejects_wrong_csrf(
    real_login: LoginResult,
    http_client: httpx.Client,
    api_http_environment,
) -> None:
    del real_login
    response = http_client.post(
        "/api/v1/sessions",
        headers=same_origin_headers(
            api_http_environment.web_origin,
            csrf_token="wrong-csrf-token",
        ),
        json={"workspace_id": "not-used"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_rejected"


def test_write_rejects_a_foreign_origin(
    real_login: LoginResult,
    http_client: httpx.Client,
) -> None:
    response = http_client.post(
        "/api/v1/sessions",
        headers={
            "Origin": "http://malicious.example",
            "X-CSRF-Token": real_login.csrf_token,
        },
        json={"workspace_id": "not-used"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "origin_rejected"


def test_write_accepts_current_csrf(
    authenticated_client: httpx.Client,
    student_user: SeededUser,
) -> None:
    response = authenticated_client.post(
        "/api/v1/sessions",
        json={"workspace_id": student_user.workspace_id},
    )
    assert response.status_code == 201, response.text


def test_changing_own_password_invalidates_old_sessions(
    authenticated_client_for,
    developer_user: SeededUser,
) -> None:
    admin_client = authenticated_client_for(developer_user)
    target = create_user(admin_client, role="student", prefix="phase3password")
    target_client = authenticated_client_for(target)
    new_password = "Phase3-new-password-456!"

    changed = target_client.post(
        "/api/v1/users/me/password",
        json={"current_password": target.password, "new_password": new_password},
    )
    assert changed.status_code == 204, changed.text
    assert target_client.get("/api/v1/users/me").status_code == 401

    new_credentials = replace(target, password=new_password)
    new_client = authenticated_client_for(new_credentials)
    assert new_client.get("/api/v1/users/me").status_code == 200
