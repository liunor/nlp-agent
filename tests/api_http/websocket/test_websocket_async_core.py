"""WebSocket ticket, event delivery, replay, and isolation tests."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import httpx
import pytest
from websockets.exceptions import ConnectionClosed, InvalidStatus

from ..support.chat import submit_turn
from ..support.resources import create_session
from ..support.websocket import (
    authenticated_websocket,
    command,
    decode,
)


pytestmark = pytest.mark.api_async_core


async def _receive_until(
    websocket,
    predicate,
    *,
    timeout_s: float = 30.0,
) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        remaining = max(0.1, deadline - asyncio.get_running_loop().time())
        frame = decode(await asyncio.wait_for(websocket.recv(), remaining))
        frames.append(frame)
        if predicate(frame):
            return frames
    raise AssertionError(f"WebSocket predicate was not reached: {frames}")


async def _assert_rejected(
    uri: str,
    *,
    origin: str,
    cookie: str,
) -> None:
    try:
        async with connect_for_test(uri, origin=origin, cookie=cookie) as websocket:
            try:
                frame = await asyncio.wait_for(websocket.recv(), timeout=3)
            except ConnectionClosed as error:
                assert error.rcvd is not None
                assert error.rcvd.code in {4401, 4403}
            else:
                payload = decode(frame)
                raise AssertionError(f"rejected WebSocket became ready: {payload}")
    except InvalidStatus:
        return


class connect_for_test:
    """Small async context wrapper kept local to make rejected handshakes clear."""

    def __init__(self, uri: str, *, origin: str, cookie: str) -> None:
        self._uri = uri
        self._origin = origin
        self._cookie = cookie
        self._context = None

    async def __aenter__(self):
        from websockets.asyncio.client import connect

        self._context = connect(
            self._uri,
            origin=self._origin,
            additional_headers={"Cookie": f"nlp_session={self._cookie}"},
            proxy=None,
            open_timeout=5,
            close_timeout=5,
        )
        return await self._context.__aenter__()

    async def __aexit__(self, *args):
        assert self._context is not None
        return await self._context.__aexit__(*args)


def test_websocket_ticket_ping_and_session_subscription(
    authenticated_client: httpx.Client,
    student_user,
    web_base_url: str,
    api_http_environment,
) -> None:
    session = create_session(authenticated_client, workspace_id=student_user.workspace_id)

    async def scenario() -> None:
        async with authenticated_websocket(
            authenticated_client,
            base_url=web_base_url,
            origin=api_http_environment.web_origin,
        ) as websocket:
            ready = decode(await asyncio.wait_for(websocket.recv(), 5))
            assert ready["type"] == "connection.ready"
            assert ready["payload"]["user_id"] == student_user.user_id

            await websocket.send(command("ping", "ping-1", {"nonce": "n-1"}))
            frames = await _receive_until(
                websocket,
                lambda frame: frame["type"] == "pong",
                timeout_s=5,
            )
            assert frames[-1]["request_id"] == "ping-1"
            assert frames[-1]["payload"]["nonce"] == "n-1"

            await websocket.send(
                command(
                    "session.subscribe",
                    "subscribe-1",
                    {"session_id": session["session_id"]},
                )
            )
            frames = await _receive_until(
                websocket,
                lambda frame: frame["type"] == "command.ack"
                and frame.get("request_id") == "subscribe-1",
                timeout_s=5,
            )
            assert frames[-1]["payload"]["changed"] is True

    asyncio.run(scenario())


def test_websocket_receives_http_worker_events_and_supports_replay(
    authenticated_client: httpx.Client,
    student_user,
    web_base_url: str,
    api_http_environment,
) -> None:
    session = create_session(authenticated_client, workspace_id=student_user.workspace_id)

    async def scenario() -> None:
        async with authenticated_websocket(
            authenticated_client,
            base_url=web_base_url,
            origin=api_http_environment.web_origin,
        ) as websocket:
            ready = decode(await asyncio.wait_for(websocket.recv(), 5))
            assert ready["type"] == "connection.ready"
            await websocket.send(
                command(
                    "session.subscribe",
                    "subscribe-events",
                    {"session_id": session["session_id"]},
                )
            )
            await _receive_until(
                websocket,
                lambda frame: frame.get("request_id") == "subscribe-events",
                timeout_s=5,
            )
            accepted = await asyncio.to_thread(
                submit_turn,
                authenticated_client,
                session_id=session["session_id"],
                content="WebSocket event delivery",
            )
            frames = await _receive_until(
                websocket,
                lambda frame: frame["type"] == "chat.completed"
                and frame.get("turn_id") == accepted["turn_id"],
            )
            chat_frames = [
                frame
                for frame in frames
                if frame.get("turn_id") == accepted["turn_id"]
                and frame.get("sequence") is not None
            ]
            assert {frame["type"] for frame in chat_frames} >= {
                "chat.started",
                "chat.message.completed",
                "chat.completed",
            }
            assert [frame["sequence"] for frame in chat_frames] == sorted(
                frame["sequence"] for frame in chat_frames
            )

            await websocket.close()

        # A new single-use ticket replays the durable event log over the same
        # authenticated protocol, rather than depending on Pub/Sub history.
        async with authenticated_websocket(
            authenticated_client,
            base_url=web_base_url,
            origin=api_http_environment.web_origin,
        ) as resumed:
            await resumed.recv()
            await resumed.send(
                command(
                    "stream.resume",
                    "resume-1",
                    {"turn_id": accepted["turn_id"], "after_sequence": 0},
                )
            )
            replay = await _receive_until(
                resumed,
                lambda frame: frame.get("request_id") == "resume-1",
                timeout_s=10,
            )
            replayed = [
                frame
                for frame in replay
                if frame.get("turn_id") == accepted["turn_id"]
                and frame.get("sequence") is not None
            ]
            assert replayed
            assert replay[-1]["type"] == "command.ack"
            assert replay[-1]["payload"]["replayed"] == len(replayed)

    asyncio.run(scenario())


def test_websocket_chat_send_command_is_consumed_by_the_worker(
    authenticated_client: httpx.Client,
    student_user,
    web_base_url: str,
    api_http_environment,
) -> None:
    session = create_session(authenticated_client, workspace_id=student_user.workspace_id)

    async def scenario() -> None:
        async with authenticated_websocket(
            authenticated_client,
            base_url=web_base_url,
            origin=api_http_environment.web_origin,
        ) as websocket:
            assert decode(await websocket.recv())["type"] == "connection.ready"
            await websocket.send(
                command(
                    "chat.send",
                    "chat-send-1",
                    {
                        "session_id": session["session_id"],
                        "content": "WebSocket command to Worker",
                    },
                )
            )
            frames = await _receive_until(
                websocket,
                lambda frame: frame.get("request_id") == "chat-send-1",
                timeout_s=10,
            )
            ack = frames[-1]
            assert ack["type"] == "command.ack"
            assert ack["payload"]["command"] == "chat.send"
            turn_id = str(ack["turn_id"])
            completed = await _receive_until(
                websocket,
                lambda frame: frame["type"] == "chat.completed"
                and frame.get("turn_id") == turn_id,
                timeout_s=30,
            )
            assert any(frame["type"] == "chat.started" for frame in completed)

    asyncio.run(scenario())


def test_websocket_ticket_is_single_use_and_origin_bound(
    authenticated_client: httpx.Client,
    mysql_probe,
    web_base_url: str,
    api_http_environment,
) -> None:
    ticket_response = authenticated_client.post("/api/v1/auth/ws-ticket")
    assert ticket_response.status_code == 200, ticket_response.text
    ticket = ticket_response.json()["ticket"]
    cookie = authenticated_client.cookies.get("nlp_session")
    assert cookie
    uri = f"{web_base_url.replace('http://', 'ws://', 1)}/ws/v1?ticket={ticket}"

    async def scenario() -> None:
        async with connect_for_test(
            uri,
            origin=api_http_environment.web_origin,
            cookie=cookie,
        ) as websocket:
            assert decode(await websocket.recv())["type"] == "connection.ready"
        await _assert_rejected(
            uri,
            origin=api_http_environment.web_origin,
            cookie=cookie,
        )

        fresh_ticket = authenticated_client.post("/api/v1/auth/ws-ticket").json()["ticket"]
        fresh_uri = f"{web_base_url.replace('http://', 'ws://', 1)}/ws/v1?ticket={fresh_ticket}"
        await _assert_rejected(
            fresh_uri,
            origin="http://evil.invalid",
            cookie=cookie,
        )

        missing_uri = f"{web_base_url.replace('http://', 'ws://', 1)}/ws/v1"
        await _assert_rejected(
            missing_uri,
            origin=api_http_environment.web_origin,
            cookie=cookie,
        )
        invalid_uri = f"{missing_uri}?ticket=invalid-ticket"
        await _assert_rejected(
            invalid_uri,
            origin=api_http_environment.web_origin,
            cookie=cookie,
        )

        expiring_ticket = authenticated_client.post(
            "/api/v1/auth/ws-ticket"
        ).json()["ticket"]
        mysql_probe.execute(
            "UPDATE nlp_ws_tickets SET expires_at=UTC_TIMESTAMP(6)-INTERVAL 1 SECOND "
            "WHERE ticket_hash=:ticket_hash",
            ticket_hash=hashlib.sha256(expiring_ticket.encode("ascii")).hexdigest(),
        )
        expiring_uri = (
            f"{web_base_url.replace('http://', 'ws://', 1)}/ws/v1?ticket={expiring_ticket}"
        )
        await _assert_rejected(
            expiring_uri,
            origin=api_http_environment.web_origin,
            cookie=cookie,
        )

    asyncio.run(scenario())


def test_websocket_hides_foreign_session_and_reacts_to_session_revoke(
    authenticated_client: httpx.Client,
    authenticated_client_for,
    student_user,
    teacher_user,
    mysql_probe,
    web_base_url: str,
    api_http_environment,
) -> None:
    own_session = create_session(authenticated_client, workspace_id=student_user.workspace_id)
    teacher_client = authenticated_client_for(teacher_user)
    foreign_session = create_session(teacher_client, workspace_id=teacher_user.workspace_id)
    from ..support.auth import session_id_for_client

    own_auth_session = session_id_for_client(authenticated_client, mysql_probe)
    revoke_client = authenticated_client_for(student_user)

    async def scenario() -> None:
        async with authenticated_websocket(
            authenticated_client,
            base_url=web_base_url,
            origin=api_http_environment.web_origin,
        ) as websocket:
            assert decode(await websocket.recv())["type"] == "connection.ready"
            await websocket.send(
                command(
                    "session.subscribe",
                    "foreign-1",
                    {"session_id": foreign_session["session_id"]},
                )
            )
            error_frames = await _receive_until(
                websocket,
                lambda frame: frame.get("request_id") == "foreign-1",
                timeout_s=5,
            )
            error = error_frames[-1]
            assert error["type"] == "command.error"
            assert error["payload"]["code"] == "not_found"

            revoked = revoke_client.delete(f"/api/v1/auth/sessions/{own_auth_session}")
            assert revoked.status_code == 204, revoked.text
            await websocket.send(command("ping", "after-revoke", {"nonce": "x"}))
            try:
                await asyncio.wait_for(websocket.recv(), 5)
            except ConnectionClosed as closed:
                assert closed.rcvd is not None
                assert closed.rcvd.code == 4401
            else:
                raise AssertionError("revoked WebSocket session remained usable")

    asyncio.run(scenario())
