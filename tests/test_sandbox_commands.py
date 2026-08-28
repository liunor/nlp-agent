from __future__ import annotations

import time

import pytest


@pytest.mark.asyncio
async def test_manager_command_store_round_trips_pool_target() -> None:
    from server.sandbox.commands import RedisSandboxManagerCommandStore

    class FakeRedis:
        def __init__(self) -> None:
            self.rows: list[tuple[str, dict[str, str]]] = []
            self.values: dict[str, str] = {}

        async def xadd(self, _stream: str, fields: dict[str, str], **_kwargs: object) -> str:
            identifier = f"0-{len(self.rows) + 1}"
            self.rows.append((identifier, fields))
            return identifier

        async def xread(self, _streams: dict[str, str], **_kwargs: object):
            return [("nova:sandbox:manager:commands", self.rows)]

        async def get(self, key: str):
            return self.values.get(key)

        async def set(self, key: str, value: str, **kwargs: object):
            if kwargs.get("nx") and key in self.values:
                return False
            self.values[key] = value
            return True

    store = RedisSandboxManagerCommandStore(FakeRedis())
    command_id = await store.request_pool_target(
        profile_id="python-base", target=4, reason="class-start"
    )
    cursor, commands = await store.read()
    assert command_id == "0-1"
    assert cursor == "0-1"
    assert commands[0]["target"] == "4"
    assert commands[0]["reason"] == "class-start"
    assert float(commands[0]["expires_at"]) > time.time()
    assert await store.load_cursor() == "0-0"
    await store.save_cursor("0-1")
    assert await store.load_cursor() == "0-1"
    assert await store.is_handled("0-1") is False
    assert await store.mark_handled("0-1") is True
    assert await store.is_handled("0-1") is True
    assert await store.mark_handled("0-1") is False


def test_manager_command_expiry_fails_closed() -> None:
    from server.sandbox.commands import command_expired

    assert command_expired({}) is True
    assert command_expired({"expires_at": "not-a-timestamp"}) is True
    assert command_expired({"expires_at": "1"}, now=2) is True
    assert command_expired({"expires_at": "3"}, now=2) is False


@pytest.mark.asyncio
async def test_pool_target_is_marked_only_after_refill_succeeds() -> None:
    from server.sandbox.manager_runner import process_manager_command

    class CommandStore:
        def __init__(self) -> None:
            self.marked = 0

        async def is_handled(self, _command_id: str) -> bool:
            return False

        async def mark_handled(self, _command_id: str) -> bool:
            self.marked += 1
            return True

    class Manager:
        def __init__(self) -> None:
            self.refills = 0

        async def request_target(self, target: int) -> None:
            assert target == 4

        async def refill(self) -> int:
            self.refills += 1
            if self.refills == 1:
                raise RuntimeError("database unavailable")
            return 1

        def _trace(self, *_args: object, **_kwargs: object) -> None:
            return None

    command = {
        "id": "0-1",
        "type": "pool_target",
        "profile_id": "python-base",
        "target": "4",
        "expires_at": str(time.time() + 60),
    }
    store = CommandStore()
    manager = Manager()

    assert await process_manager_command(command, command_store=store, manager=manager) is False
    assert store.marked == 0
    assert await process_manager_command(command, command_store=store, manager=manager) is True
    assert store.marked == 1
