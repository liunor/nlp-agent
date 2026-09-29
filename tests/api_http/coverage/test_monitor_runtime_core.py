"""Real Monitor HTTP coverage with the production DB session cookie."""

from __future__ import annotations

import httpx
import pytest

from ..support.auth import same_origin_headers
from ..support.environment import SeededUser
from ..support.http import json_response, problem_response


pytestmark = pytest.mark.api_core


def _http_error(response: httpx.Response, expected_status: int) -> dict:
    assert response.status_code == expected_status, response.text
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def _authenticate_monitor(
    monitor_http_client: httpx.Client,
    user: SeededUser,
    monitor_base_url: str,
) -> dict:
    login = monitor_http_client.post(
        "/api/v1/auth/login",
        headers=same_origin_headers(monitor_base_url),
        json={"username": user.username, "password": user.password},
    )
    login_payload = json_response(login, 200)
    assert login_payload["user_id"] == user.user_id
    session = monitor_http_client.get(
        "/api/v1/auth/session",
        headers=same_origin_headers(monitor_base_url),
    )
    payload = json_response(session, 200)
    monitor_http_client.headers.update(
        same_origin_headers(monitor_base_url, csrf_token=payload["csrf_token"])
    )
    return payload


def _assert_no_sensitive_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            assert str(key).lower() not in {
                "password",
                "api_key",
                "authorization",
                "access_token",
                "secret",
            }
            _assert_no_sensitive_keys(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_sensitive_keys(item)


@pytest.mark.parametrize("role", ["guest", "student", "teacher"])
def test_monitor_permission_is_developer_only(
    monitor_http_client: httpx.Client,
    monitor_base_url: str,
    guest_user: SeededUser,
    student_user: SeededUser,
    teacher_user: SeededUser,
    role: str,
) -> None:
    user = {
        "guest": guest_user,
        "student": student_user,
        "teacher": teacher_user,
    }[role]
    response = monitor_http_client.post(
        "/api/v1/auth/login",
        headers=same_origin_headers(monitor_base_url),
        json={"username": user.username, "password": user.password},
    )
    problem = problem_response(response, 403)
    assert problem["code"] == "forbidden"


def test_monitor_observability_and_sandbox_routes_are_real_and_bounded(
    authenticated_client_for,
    developer_user: SeededUser,
    monitor_http_client: httpx.Client,
    monitor_base_url: str,
) -> None:
    session = _authenticate_monitor(
        monitor_http_client, developer_user, monitor_base_url
    )
    assert session["user_id"] == developer_user.user_id
    assert "developer" in session["roles"]

    for path in (
        "/api/v1/observability/overview?days=1",
        "/api/v1/observability/usage?days=1",
            "/api/v1/observability/dependencies",
        "/api/v1/observability/events?limit=1&level=error",
        "/api/v1/observability/errors?days=1&limit=1",
        "/api/v1/observability/storage",
        "/api/v1/observability/sandbox/overview",
        "/api/v1/observability/sandbox/logs?limit=1&since_seconds=60",
        "/api/v1/observability/sandbox/runtimes",
        "/api/v1/observability/sandbox/executions?status_filter=completed",
        "/api/v1/observability/sandbox/preload-compatibility",
    ):
        payload = json_response(monitor_http_client.get(path), 200)
        _assert_no_sensitive_keys(payload)

    traces = json_response(
        monitor_http_client.get("/api/v1/observability/traces?limit=1&status=completed"),
        200,
    )
    assert isinstance(traces["items"], list)
    problem_response(
        monitor_http_client.get("/api/v1/observability/traces/not-a-real-trace"),
        404,
    )
    _http_error(
        monitor_http_client.get("/api/v1/observability/sandbox/runtimes/not-a-real-runtime"),
        404,
    )
    _http_error(
        monitor_http_client.get("/api/v1/observability/sandbox/executions/not-a-real-execution/events"),
        404,
    )

    prewarm_without_csrf = monitor_http_client.post(
        "/api/v1/observability/sandbox/capacity/prewarm",
        headers={"Origin": monitor_base_url, "X-CSRF-Token": ""},
        json={"expected_sessions": 1, "sessions_per_runtime": 1, "profile_id": "python-base"},
    )
    problem = problem_response(prewarm_without_csrf, 403)
    assert problem["code"] == "csrf_rejected"
    prewarm = json_response(
        monitor_http_client.post(
            "/api/v1/observability/sandbox/capacity/prewarm",
            json={"expected_sessions": 1, "sessions_per_runtime": 1, "profile_id": "python-base"},
        ),
        200,
    )
    assert prewarm["command_id"]
    assert prewarm["profile_id"] == "python-base"

    prune = json_response(
        monitor_http_client.post(
            "/api/v1/observability/storage/prune?trace_days=1&event_days=1"
        ),
        200,
    )
    assert prune["database"] == "mysql"
    _assert_no_sensitive_keys(prune)

    _http_error(
        monitor_http_client.post(
            "/api/v1/observability/sandbox/runtimes/not-a-real-runtime/drain"
        ),
        404,
    )
