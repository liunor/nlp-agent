from __future__ import annotations

import asyncio

import pytest

from server.user.tencent_sms import (
    BoundedSmsSender,
    SmsGatewayBusyError,
    SmsGatewayTimeoutError,
)


@pytest.mark.asyncio
async def test_sms_sender_rejects_excess_concurrency_without_queueing() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class Provider:
        async def send_verification_code(self, _phone: str, _code: str) -> bool:
            entered.set()
            await release.wait()
            return True

    sender = BoundedSmsSender(max_concurrency=1, timeout_s=1)
    first = asyncio.create_task(sender.send(Provider(), "phone-1", "123456"))
    await entered.wait()

    with pytest.raises(SmsGatewayBusyError):
        await sender.send(Provider(), "phone-2", "123456")

    release.set()
    assert await first is True


@pytest.mark.asyncio
async def test_timed_out_sms_keeps_slot_until_underlying_provider_finishes() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class Provider:
        async def send_verification_code(self, _phone: str, _code: str) -> bool:
            entered.set()
            await release.wait()
            return True

    sender = BoundedSmsSender(max_concurrency=1, timeout_s=0.1)
    with pytest.raises(SmsGatewayTimeoutError):
        await sender.send(Provider(), "phone-1", "123456")
    await entered.wait()

    with pytest.raises(SmsGatewayBusyError):
        await sender.send(Provider(), "phone-2", "123456")

    release.set()
    for _ in range(10):
        await asyncio.sleep(0)
        if not sender._semaphore.locked():
            break

    class FastProvider:
        async def send_verification_code(self, _phone: str, _code: str) -> bool:
            return True

    assert await sender.send(FastProvider(), "phone-3", "123456")
