"""A delayed authorization notification must not evict the new valid socket."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from core.identity import AuthenticatedPrincipal
from server.web.auth import AuthenticationError
from server.web.websocket import WebSocketConnection, WebSocketHub


def connection(user="target", version=2, current=2, failure=None):
    check = AsyncMock(side_effect=failure)
    item = WebSocketConnection(
        None, None, AuthenticatedPrincipal(user_id=user, authorization_version=version),
        max_queue=1, send_queue_size=1, send_timeout_s=1,
        session_check=check,
        authorization_refresh=AsyncMock(return_value=AuthenticatedPrincipal(user_id=user, authorization_version=current)),
    )
    item.close = AsyncMock()
    return item, check


@pytest.mark.asyncio
async def test_delayed_repeated_notice_closes_old_socket_but_preserves_current_and_other_user():
    hub = WebSocketHub()
    old, old_check = connection(version=1)
    current, current_check = connection()
    other, other_check = connection(user="other", version=1)
    for item in (old, current, other):
        assert hub.try_add(item)
    await hub.revalidate_user("target")
    old.close.assert_awaited_once_with(code=4403, reason="authorization changed")
    current.close.assert_not_awaited()
    other.close.assert_not_awaited()
    other_check.assert_not_awaited()
    old_check.assert_awaited_once_with()
    await hub.revalidate_user("target")
    current.close.assert_not_awaited()
    assert current_check.await_count == 2


@pytest.mark.asyncio
async def test_revoked_session_is_closed_even_when_principal_version_matches():
    hub = WebSocketHub()
    item, _ = connection(failure=AuthenticationError("revoked"))
    hub.try_add(item)
    await hub.revalidate_user("target")
    item.close.assert_awaited_once_with(code=4403, reason="authorization changed")


@pytest.mark.asyncio
async def test_database_failure_fails_closed_without_disrupting_other_checks():
    hub = WebSocketHub()
    unavailable, _ = connection(failure=RuntimeError("isolated database failure"))
    valid, _ = connection()
    hub.try_add(unavailable)
    hub.try_add(valid)
    await hub.revalidate_user("target")
    unavailable.close.assert_awaited_once_with(code=1013, reason="authorization check unavailable")
    valid.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_notification_validation_remains_cancellable():
    hub = WebSocketHub()
    entered = asyncio.Event()

    async def wait():
        entered.set()
        await asyncio.Event().wait()

    item, _ = connection()
    item._session_check = wait
    hub.try_add(item)
    task = asyncio.create_task(hub.revalidate_user("target"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    item.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_administrator_close_still_closes_all_user_sockets():
    hub = WebSocketHub()
    item, check = connection()
    hub.try_add(item)
    await hub.close_user("target")
    item.close.assert_awaited_once_with(code=4403, reason="authorization changed")
    check.assert_not_awaited()
