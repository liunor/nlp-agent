"""Production WebSocket helpers for the isolated HTTP suite."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from websockets.asyncio.client import ClientConnection, connect


@asynccontextmanager
async def authenticated_websocket(
    client: httpx.Client,
    *,
    base_url: str,
    origin: str,
) -> AsyncIterator[ClientConnection]:
    ticket_response = client.post("/api/v1/auth/ws-ticket")
    assert ticket_response.status_code == 200, ticket_response.text
    ticket = str(ticket_response.json()["ticket"])
    cookie = client.cookies.get("nlp_session")
    assert cookie
    environment = getattr(client, "_api_http_environment", None)
    if environment is not None:
        environment.record_websocket_route("/ws/v1")
    uri = base_url.replace("http://", "ws://", 1) + f"/ws/v1?ticket={ticket}"
    async with connect(
        uri,
        origin=origin,
        additional_headers={"Cookie": f"nlp_session={cookie}"},
        proxy=None,
        open_timeout=10,
        close_timeout=5,
    ) as websocket:
        yield websocket


def command(
    command_type: str,
    request_id: str,
    payload: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "v": "1",
            "type": command_type,
            "request_id": request_id,
            "payload": payload,
        },
        separators=(",", ":"),
    )


def decode(frame: str | bytes) -> dict[str, Any]:
    payload = json.loads(frame)
    assert isinstance(payload, dict)
    return payload
