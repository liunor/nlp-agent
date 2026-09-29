"""Behavior and security contracts for the real Monitor HTTP service."""

from __future__ import annotations

import uuid

import pytest

from ..support.environment import SeededUser
from ..support.http import json_response, problem_response
from .test_monitor_runtime_core import _assert_no_sensitive_keys, _authenticate_monitor


pytestmark = pytest.mark.api_full


def test_monitor_auth_session_ticket_and_audit_contracts(
    monitor_http_client,
    monitor_base_url: str,
    developer_user: SeededUser,
) -> None:
    session = _authenticate_monitor(
        monitor_http_client,
        developer_user,
        monitor_base_url,
    )
    assert session["user_id"] == developer_user.user_id
    assert "developer" in session["roles"]
    assert monitor_http_client.cookies.get("nlp_monitor_session")

    # Production monitor sessions are created by the control-plane login.  A
    # database-backed client must not silently fall back to the legacy injected
    # session endpoint.
    session_create = monitor_http_client.post("/api/v1/auth/session")
    problem_response(session_create, 401)

    ticket = json_response(
        monitor_http_client.post("/api/v1/auth/ws-ticket"),
        200,
    )
    assert isinstance(ticket["ticket"], str) and ticket["ticket"]
    assert ticket["expires_in"] == 60

    audit = json_response(
        monitor_http_client.get(
            "/api/v1/audit/authorization?limit=5&offset=0&decision=allow"
        ),
        200,
    )
    assert set(("items", "total", "offset", "limit", "has_more")) <= audit.keys()
    assert audit["offset"] == 0
    assert audit["limit"] == 5
    stats = json_response(
        monitor_http_client.get("/api/v1/audit/authorization/stats?days=1"),
        200,
    )
    assert stats["period_days"] == 1
    assert isinstance(stats["by_decision"], dict)


def test_monitor_observability_read_models_and_boundaries(
    monitor_http_client,
    monitor_base_url: str,
    developer_user: SeededUser,
) -> None:
    _authenticate_monitor(monitor_http_client, developer_user, monitor_base_url)

    cases = {
        "/api/v1/observability/overview?days=1": ("runtime", "analysis"),
        "/api/v1/observability/dependencies?days=1&window_minutes=10&bucket_minutes=5": ("catalog",),
        "/api/v1/observability/traces?limit=5&status=completed": ("items",),
        "/api/v1/observability/traces/groups?days=1&limit=5&focus=all": (),
        "/api/v1/observability/usage?days=1": ("items",),
        "/api/v1/observability/usage/system?days=1": ("catalog",),
        "/api/v1/observability/usage/system/users?days=1&limit=5&offset=0": (),
        "/api/v1/observability/usage/system/dimensions?dimension=providers&days=1&limit=5": (),
        "/api/v1/observability/usage/system/trend?window_minutes=10&bucket_minutes=5": (),
        "/api/v1/observability/usage-shadow?days=1": (),
        "/api/v1/observability/events?limit=5&level=error": ("items",),
        "/api/v1/observability/errors?days=1&limit=5&window_minutes=10&bucket_minutes=5": (),
        "/api/v1/observability/storage": ("retention",),
        "/api/v1/observability/sandbox/overview": (),
        "/api/v1/observability/sandbox/logs?limit=5&since_seconds=60": (),
        "/api/v1/observability/sandbox/runtimes": (),
        "/api/v1/observability/sandbox/executions?status_filter=completed": (),
        "/api/v1/observability/sandbox/preload-compatibility": (),
    }
    for path, required_keys in cases.items():
        payload = json_response(monitor_http_client.get(path), 200)
        assert isinstance(payload, dict)
        assert set(required_keys) <= payload.keys(), (path, payload)
        _assert_no_sensitive_keys(payload)

    invalid_dependency_window = monitor_http_client.get(
        "/api/v1/observability/dependencies?window_minutes=5&bucket_minutes=6"
    )
    dependency_problem = problem_response(invalid_dependency_window, 422)
    assert dependency_problem["code"] == "invalid_dependency_window"
    invalid_usage_window = monitor_http_client.get(
        "/api/v1/observability/usage/system/trend?window_minutes=5&bucket_minutes=6"
    )
    usage_problem = problem_response(invalid_usage_window, 422)
    assert usage_problem["code"] == "invalid_usage_window"

    unknown_trace = monitor_http_client.get(
        f"/api/v1/observability/traces/{uuid.uuid4()}"
    )
    assert problem_response(unknown_trace, 404)["code"] == "trace_not_found"
    unknown_chain = monitor_http_client.get(
        f"/api/v1/observability/traces/chains/{uuid.uuid4()}"
    )
    assert problem_response(unknown_chain, 404)["code"] == "trace_group_not_found"
    unknown_runtime = monitor_http_client.get(
        "/api/v1/observability/sandbox/runtimes/not-a-runtime"
    )
    assert unknown_runtime.status_code == 404
    unknown_execution_events = monitor_http_client.get(
        "/api/v1/observability/sandbox/executions/not-an-execution/events"
    )
    assert unknown_execution_events.status_code == 404
