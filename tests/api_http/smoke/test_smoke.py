"""Minimal black-box smoke checks against real Web and Monitor processes."""

from __future__ import annotations

import httpx
import pytest

from ..support.auth import LoginResult
from ..support.environment import SeededUser


pytestmark = pytest.mark.api_smoke


def test_web_liveness(http_client: httpx.Client) -> None:
    response = http_client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_web_readiness(http_client: httpx.Client) -> None:
    response = http_client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_real_login_persists_database_cookie(
    real_login: LoginResult,
    http_client: httpx.Client,
) -> None:
    assert real_login.user_id
    assert real_login.cookie_name in http_client.cookies


def test_real_session_returns_database_identity(
    authenticated_client: httpx.Client,
    student_user: SeededUser,
) -> None:
    response = authenticated_client.get("/api/v1/auth/session")
    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == student_user.user_id
    assert "student" in payload["roles"]
    assert payload["csrf_token"]


def test_real_authenticated_client_can_create_session(
    authenticated_client: httpx.Client,
    student_user: SeededUser,
) -> None:
    response = authenticated_client.post(
        "/api/v1/sessions",
        json={"workspace_id": student_user.workspace_id},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["workspace_id"] == student_user.workspace_id
    assert payload["user_id"] == student_user.user_id


def test_real_login_can_issue_database_websocket_ticket(
    authenticated_client: httpx.Client,
) -> None:
    response = authenticated_client.post("/api/v1/auth/ws-ticket")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload["ticket"], str)
    assert payload["ticket"]
    assert payload["expires_in"] == 60


def test_monitor_readiness(monitor_http_client: httpx.Client) -> None:
    response = monitor_http_client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
