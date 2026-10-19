"""Do not hide ticket transport failures or leak connections before ready."""
import asyncio
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from tests.load import t07_classroom_load as load


@pytest.mark.parametrize("phase", ["ticket", "upgrade", "ready", "cancel", "success"])
def test_setup_reports_actual_stage_and_closes_only_failed_owned_socket(monkeypatch, phase):
    response = httpx.Response(200, json={"ticket": "private-ticket"})
    client = AsyncMock()
    client.post.return_value = response
    client.cookies = Mock()
    client.cookies.get.return_value = "private-cookie"
    socket = AsyncMock()
    socket.recv.return_value = '{"type":"connection.ready"}'
    upgrade = AsyncMock(return_value=socket)
    monkeypatch.setattr(load, "connect", upgrade)
    if phase == "ticket":
        client.post.side_effect = httpx.RemoteProtocolError("private-server-message")
    elif phase == "upgrade":
        upgrade.side_effect = OSError("private-server-message")
    elif phase == "ready":
        socket.recv.side_effect = TimeoutError("private-server-message")
    elif phase == "cancel":
        socket.recv.side_effect = asyncio.CancelledError()
    user = {"username": "fixture", "client": client, "csrf": "private-csrf"}
    call = load._open_websocket(user, base_url="http://127.0.0.1:1", origin="http://127.0.0.1:1")
    if phase == "cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(call)
    else:
        result, metrics = asyncio.run(call)
        if phase == "success":
            assert result is socket and metrics["status"] == "ready"
        else:
            assert result is None and metrics["status"] == "error"
            assert metrics["stage"] == {"ticket": "ticket_request", "upgrade": "websocket_upgrade", "ready": "connection_ready"}[phase]
            assert "private-" not in str(metrics)
    assert client.post.await_count == 1  # No automatic retry conceals the failure.
    assert socket.close.await_count == int(phase in {"ready", "cancel"})
