"""Assertions shared by the real HTTP contract tests."""

from __future__ import annotations

from typing import Any

import httpx


def json_response(response: httpx.Response, expected_status: int) -> dict[str, Any] | list[Any]:
    """Assert the transport contract before returning a decoded payload."""
    assert response.status_code == expected_status, response.text
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith("application/json"), content_type
    payload = response.json()
    assert isinstance(payload, (dict, list))
    return payload


def problem_response(response: httpx.Response, expected_status: int) -> dict[str, Any]:
    assert response.status_code == expected_status, response.text
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith("application/problem+json"), content_type
    payload = response.json()
    assert isinstance(payload, dict)
    assert "code" in payload
    return payload
