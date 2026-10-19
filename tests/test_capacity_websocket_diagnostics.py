"""Do not hide ticket transport failures or leak connections before ready."""
import asyncio
import time
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


def test_ticket_trace_retains_no_http_headers_or_exception_details(monkeypatch):
    client = AsyncMock()
    async def fail_ticket(*args, **kwargs):
        observe = kwargs["extensions"]["trace"]
        await observe("http11.receive_response_headers.failed", {"exception": "private-server-message"})
        await observe("private-event", {"Cookie": "private-cookie"})
        raise httpx.RemoteProtocolError("private-exception")
    client.post.side_effect = fail_ticket
    upgrade = AsyncMock()
    monkeypatch.setattr(load, "connect", upgrade)
    result, metrics = asyncio.run(load._open_websocket(
        {"username": "fixture", "client": client, "csrf": "private-csrf",
         "last_http_completed_at": time.monotonic() - 5.1},
        base_url="http://127.0.0.1:1", origin="http://127.0.0.1:1"))
    assert result is None and metrics["stage"] == "ticket_request"
    assert metrics["ticket_trace"] == ["http11.receive_response_headers.failed"]
    assert metrics["ticket_connection_reused"] and metrics["idle_before_ticket_ms"] >= 5100
    assert "private-" not in str(metrics)
    upgrade.assert_not_awaited()
