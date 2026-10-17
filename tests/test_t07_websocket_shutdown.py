"""Exercise transport shutdown, including cancellation during connection cleanup."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from core.identity import AuthenticatedPrincipal
from server.web.websocket import WebSocketConnection
from tests.test_web_api import authenticate, web_app  # noqa: F401,F811


def test_repeated_browser_disconnect_after_deleted_subscription(web_app):  # noqa: F811
    app, _engine = web_app
    with TestClient(app) as client:
        authenticate(client)
        for index in range(100):
            with client.websocket_connect("/ws/v1", headers={"Origin": "http://testserver"}) as websocket:
                assert websocket.receive_json()["type"] == "connection.ready"
                websocket.send_json({"v": "1", "type": "session.subscribe", "request_id": str(index), "payload": {"session_id": "deleted-session"}})
                assert websocket.receive_json()["payload"]["code"] == "not_found"
                websocket.send_json({"v": "1", "type": "ping", "request_id": str(index), "payload": {}})
                assert websocket.receive_json()["type"] == "pong"


@pytest.mark.asyncio
async def test_cancelled_close_can_be_joined_without_leaking_sender():
    websocket = AsyncMock()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def close(**_kwargs):
        entered.set()
        await release.wait()

    websocket.close.side_effect = close
    connection = WebSocketConnection(websocket, None, AuthenticatedPrincipal(user_id="test"), max_queue=10, send_queue_size=10, send_timeout_s=1)
    connection.start()
    first = asyncio.create_task(connection.close())
    await entered.wait()
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    assert first.cancelled(), "cancelling a close caller must still propagate to that caller"
    second = asyncio.create_task(connection.close())
    await asyncio.sleep(0)
    assert not second.done(), "another close must join cleanup instead of accepting a half-closed connection"
    release.set()
    await asyncio.wait_for(second, 1)
    assert connection._sender_task.done()


@pytest.mark.asyncio
async def test_cancellation_during_accept_releases_hub_slot():
    from server.web.auth import SameOriginSessionAuth
    from server.web.websocket import WebSocketHub, websocket_endpoint

    auth = SameOriginSessionAuth(secret="test-only-secret-long-enough-for-session-hmac", allowed_origins=["http://testserver"])
    token, _claims = auth.issue()
    websocket = AsyncMock()
    websocket.headers = {"origin": "http://testserver", "host": "testserver"}
    websocket.cookies = {auth.cookie_name: token}
    websocket.accept.side_effect = asyncio.CancelledError()
    hub = WebSocketHub(max_connections=10, max_connections_per_user=2)
    await websocket_endpoint(websocket, gateway=None, auth=auth, hub=hub, max_message_bytes=1000, max_queue=10, send_queue_size=10, send_timeout_s=1)
    assert not hub._connections
