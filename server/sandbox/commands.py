"""Redis command queue between the Web control plane and isolated Manager."""

from __future__ import annotations

import time
from typing import Any

from configs.settings import settings
from .faults import SandboxFaultInjector


def command_expired(command: dict[str, str], *, now: float | None = None) -> bool:
    """Treat missing or malformed command expiry as expired (fail closed)."""
    raw = command.get("expires_at")
    try:
        expires_at = float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        return True
    return expires_at <= (time.time() if now is None else now)


class RedisSandboxManagerCommandStore:
    def __init__(
        self,
        client: Any,
        *,
        stream: str = "nova:sandbox:manager:commands",
        fault_injector: SandboxFaultInjector | None = None,
    ) -> None:
        self._client = client
        self._stream = stream
        self._faults = fault_injector or SandboxFaultInjector.from_env()

    @property
    def _cursor_key(self) -> str:
        return f"{self._stream}:cursor"

    def _handled_key(self, command_id: str) -> str:
        return f"{self._stream}:handled:{command_id}"

    async def request_pool_target(
        self,
        *,
        profile_id: str,
        target: int,
        reason: str,
        execute_at: str | None = None,
    ) -> str:
        self._faults.fail_if_configured("redis.xadd")
        issued_at = time.time()
        expires_at = issued_at + max(60, settings.NLP_AGENT_SANDBOX_COMMAND_RETENTION_S)
        command_id = await self._client.xadd(
            self._stream,
            {
                "type": "pool_target",
                "profile_id": profile_id,
                "target": str(target),
                "reason": reason,
                "execute_at": execute_at or "",
                "issued_at": str(issued_at),
                "expires_at": str(expires_at),
            },
            maxlen=1_000,
            approximate=True,
        )
        return command_id.decode("utf-8") if isinstance(command_id, bytes) else str(command_id)

    async def read(
        self,
        *,
        after_id: str = "0-0",
        count: int = 20,
        block_ms: int = 1000,
    ) -> tuple[str, list[dict[str, str]]]:
        self._faults.fail_if_configured("redis.xread")
        rows = await self._client.xread({self._stream: after_id}, count=count, block=max(1, block_ms))
        if not rows:
            return after_id, []
        commands: list[dict[str, str]] = []
        latest = after_id
        for _stream, messages in rows:
            for message_id, fields in messages:
                latest = message_id.decode("utf-8") if isinstance(message_id, bytes) else str(message_id)
                parsed = {
                    (key.decode("utf-8") if isinstance(key, bytes) else str(key)):
                    (value.decode("utf-8") if isinstance(value, bytes) else str(value))
                    for key, value in fields.items()
                }
                parsed["id"] = latest
                commands.append(parsed)
        return latest, commands

    async def load_cursor(self) -> str:
        value = await self._client.get(self._cursor_key)
        if value is None:
            return "0-0"
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    async def save_cursor(self, cursor: str) -> None:
        await self._client.set(
            self._cursor_key,
            cursor,
            ex=max(60, settings.NLP_AGENT_SANDBOX_COMMAND_RETENTION_S),
        )

    async def is_handled(self, command_id: str) -> bool:
        """Check completion without claiming a command before its side effect."""
        value = await self._client.get(self._handled_key(command_id))
        return value is not None

    async def mark_handled(self, command_id: str) -> bool:
        accepted = await self._client.set(
            self._handled_key(command_id),
            "1",
            nx=True,
            ex=max(60, settings.NLP_AGENT_SANDBOX_COMMAND_RETENTION_S),
        )
        return bool(accepted)

    async def close(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()


def create_sandbox_manager_command_store() -> RedisSandboxManagerCommandStore | None:
    redis_url = settings.NLP_AGENT_REDIS_URL.strip()
    if not redis_url:
        return None
    from redis.asyncio import Redis

    return RedisSandboxManagerCommandStore(Redis.from_url(redis_url, decode_responses=True))
