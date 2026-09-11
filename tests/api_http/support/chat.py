"""Polling helpers for real HTTP chat turn assertions."""

from __future__ import annotations

import time
from typing import Any

import httpx


TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}


def submit_turn(
    client: httpx.Client,
    *,
    session_id: str,
    content: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"session_id": session_id, "content": content}
    if idempotency_key is not None:
        body["idempotency_key"] = idempotency_key
    response = client.post("/api/v1/chat/turns", json=body)
    assert response.status_code == 202, response.text
    assert response.headers.get("content-type", "").startswith("application/json")
    payload = response.json()
    assert {"turn_id", "session_id", "status"}.issubset(payload)
    return payload


def wait_for_turn(
    client: httpx.Client,
    turn_id: str,
    *,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last_payload: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/chat/turns/{turn_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert isinstance(payload, dict)
        last_payload = payload
        if str(payload.get("status")) in TERMINAL_STATUSES:
            return payload
        time.sleep(0.2)
    raise AssertionError(
        f"turn {turn_id} did not reach a terminal status in {timeout_s}s: {last_payload}"
    )


def event_types(client: httpx.Client, turn_id: str) -> list[str]:
    response = client.get(f"/api/v1/chat/turns/{turn_id}/events")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload, dict)
    return [str(item["type"]) for item in payload["items"]]


def wait_for_event(
    client: httpx.Client,
    turn_id: str,
    event_type: str,
    *,
    timeout_s: float = 30.0,
) -> list[str]:
    deadline = time.monotonic() + timeout_s
    last_types: list[str] = []
    while time.monotonic() < deadline:
        last_types = event_types(client, turn_id)
        if event_type in last_types:
            return last_types
        time.sleep(0.2)
    raise AssertionError(
        f"turn {turn_id} did not publish {event_type!r}: {last_types}"
    )
