"""Monitor WebSocket ticket and heartbeat checks over the real network."""

from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from ..support.auth import same_origin_headers
from ..support.environment import SeededUser


pytestmark = pytest.mark.api_runtime_core


def _monitor_authenticate(monitor, user: SeededUser, origin: str) -> dict:
    login = monitor.post(
        "/api/v1/auth/login",
        headers=same_origin_headers(origin),
        json={"username": user.username, "password": user.password},
    )
    assert login.status_code == 200, login.text
    response = monitor.get("/api/v1/auth/session", headers=same_origin_headers(origin))
    assert response.status_code == 200, response.text
    payload = response.json()
    monitor.headers.update(same_origin_headers(origin, csrf_token=payload["csrf_token"]))
    return payload


async def _rejected(uri: str, *, origin: str, cookie: str) -> None:
    try:
        async with connect(
            uri,
            origin=origin,
            additional_headers={"Cookie": f"nlp_session={cookie}"},
            proxy=None,
            open_timeout=5,
            close_timeout=5,
        ) as websocket:
            try:
                await asyncio.wait_for(websocket.recv(), timeout=3)
            except ConnectionClosed as error:
                assert error.rcvd is not None
                assert error.rcvd.code in {4401, 4403}
            else:
                raise AssertionError("rejected Monitor WebSocket became ready")
    except InvalidStatus:
        return


def test_monitor_websocket_uses_db_ticket_heartbeat_and_single_use(
    monitor_http_client: httpx.Client,
    monitor_base_url: str,
    api_http_environment,
    developer_user: SeededUser,
    mysql_probe,
) -> None:
    session = _monitor_authenticate(monitor_http_client, developer_user, monitor_base_url)
    assert session["user_id"] == developer_user.user_id
    ticket_response = monitor_http_client.post("/api/v1/auth/ws-ticket")
    assert ticket_response.status_code == 200, ticket_response.text
    ticket = str(ticket_response.json()["ticket"])
    cookie = str(monitor_http_client.cookies.get("nlp_session"))
    uri = monitor_base_url.replace("http://", "ws://", 1) + f"/ws/observability?ticket={ticket}"

    async def scenario() -> None:
        async with connect(
            uri,
            origin=monitor_base_url,
            additional_headers={"Cookie": f"nlp_session={cookie}"},
            proxy=None,
            open_timeout=5,
            close_timeout=5,
        ) as websocket:
            deadline = asyncio.get_running_loop().time() + 4
            frames = []
            while asyncio.get_running_loop().time() < deadline:
                frame = json.loads(
                    await asyncio.wait_for(
                        websocket.recv(),
                        timeout=max(0.1, deadline - asyncio.get_running_loop().time()),
                    )
                )
                frames.append(frame)
                if frame["type"] == "monitor.heartbeat":
                    break
            assert frames[-1]["type"] == "monitor.heartbeat"
            assert all(frame["type"] in {"telemetry.event", "monitor.heartbeat"} for frame in frames)
        await _rejected(uri, origin=monitor_base_url, cookie=cookie)

    asyncio.run(scenario())

    invalid = (
        monitor_base_url.replace("http://", "ws://", 1)
        + "/ws/observability?ticket=invalid-monitor-ticket"
    )
    asyncio.run(_rejected(invalid, origin=monitor_base_url, cookie=cookie))

    expiring_response = monitor_http_client.post("/api/v1/auth/ws-ticket")
    assert expiring_response.status_code == 200, expiring_response.text
    expiring = str(expiring_response.json()["ticket"])
    mysql_probe.execute(
        "UPDATE nlp_ws_tickets SET expires_at=UTC_TIMESTAMP(6)-INTERVAL 1 SECOND "
        "WHERE ticket_hash=:ticket_hash",
        ticket_hash=hashlib.sha256(expiring.encode("ascii")).hexdigest(),
    )
    expiring_uri = (
        monitor_base_url.replace("http://", "ws://", 1)
        + f"/ws/observability?ticket={expiring}"
    )
    asyncio.run(_rejected(expiring_uri, origin=monitor_base_url, cookie=cookie))
    api_http_environment.record_websocket_route("/ws/observability")
