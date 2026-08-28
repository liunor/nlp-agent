"""Ordered sandbox execution events backed by Redis Streams in deployments."""

from __future__ import annotations

from collections import defaultdict
import asyncio
import json
from typing import Any, Callable

from configs.settings import settings
from .faults import SandboxFaultInjector


class SandboxEventStore:
    """In-memory reference semantics for event IDs and replay cursors.

    Phase 1 callers depend only on this interface; deployment wiring replaces
    this storage with Redis Streams without changing ticket or UI contracts.
    """

    def __init__(self) -> None:
        self._events: dict[str, list[dict[str, object]]] = defaultdict(list)

    async def append(
        self, execution_id: str, *, user_id: str, event_type: str, payload: dict[str, object]
    ) -> dict[str, object]:
        events = self._events[execution_id]
        event = {"event_id": str(len(events) + 1), "seq": len(events) + 1, "type": event_type, "payload": payload, "user_id": user_id}
        events.append(event)
        return {key: value for key, value in event.items() if key != "user_id"}

    async def replay(self, execution_id: str, *, user_id: str, after_event_id: str | None = None) -> list[dict[str, object]]:
        try:
            after = int(after_event_id or "0")
        except (TypeError, ValueError):
            after = 0
        return [
            {key: value for key, value in event.items() if key != "user_id"}
            for event in self._events.get(execution_id, [])
            if event["user_id"] == user_id and int(str(event["event_id"])) > after
        ]


class RedisSandboxEventStore:
    """Redis Stream implementation with bounded retention and owner filtering.

    Each execution gets its own stream.  Redis stream IDs are returned to the
    browser as cursors, so replay survives web-process restarts and works
    consistently across multiple API instances.
    """

    def __init__(
        self,
        client: Any | None = None,
        *,
        client_factory: Callable[[], Any] | None = None,
        stream_prefix: str = "nova:sandbox:events",
        retention_seconds: int = 86_400,
        max_events: int = 10_000,
        fault_injector: SandboxFaultInjector | None = None,
    ) -> None:
        if client is None and client_factory is None:
            raise ValueError("RedisSandboxEventStore requires a client or client_factory")
        if client is not None and client_factory is not None:
            raise ValueError("RedisSandboxEventStore accepts either client or client_factory")
        self._client = client
        self._client_factory = client_factory
        # redis-py async pools contain asyncio primitives.  A process can run
        # model tools on more than one event loop (pytest does this by
        # default), so a global client cannot safely be shared across loops.
        self._loop_clients: dict[asyncio.AbstractEventLoop, Any] = {}
        self._stream_prefix = stream_prefix.rstrip(":")
        self._retention_seconds = max(60, retention_seconds)
        self._max_events = max(100, max_events)
        self._faults = fault_injector or SandboxFaultInjector.from_env()

    def _stream_name(self, execution_id: str) -> str:
        return f"{self._stream_prefix}:{execution_id}"

    async def _client_for_call(self) -> Any:
        if self._client_factory is None:
            assert self._client is not None
            return self._client
        loop = asyncio.get_running_loop()
        client = self._loop_clients.get(loop)
        if client is None:
            client = self._client_factory()
            self._loop_clients[loop] = client
        return client

    async def append(self, execution_id: str, *, user_id: str, event_type: str, payload: dict[str, object]) -> dict[str, object]:
        self._faults.fail_if_configured("redis.append")
        client = await self._client_for_call()
        stream = self._stream_name(execution_id)
        event_id = await client.xadd(
            stream,
            {"user_id": user_id, "type": event_type, "payload": json.dumps(payload, separators=(",", ":"), ensure_ascii=False)},
            maxlen=self._max_events,
            approximate=True,
        )
        await client.expire(stream, self._retention_seconds)
        event_id = _text(event_id)
        return {"event_id": event_id, "seq": event_id, "type": event_type, "payload": payload}

    async def replay(self, execution_id: str, *, user_id: str, after_event_id: str | None = None) -> list[dict[str, object]]:
        self._faults.fail_if_configured("redis.read")
        client = await self._client_for_call()
        cursor = after_event_id or "0-0"
        if cursor.isdigit():
            cursor = f"0-{cursor}"
        rows = await client.xrange(self._stream_name(execution_id), min=f"({cursor}", max="+", count=self._max_events)
        events: list[dict[str, object]] = []
        for raw_id, raw_fields in rows:
            fields = {_text(key): _text(value) for key, value in raw_fields.items()}
            if fields.get("user_id") != user_id:
                continue
            try:
                payload = json.loads(fields.get("payload", "{}"))
            except json.JSONDecodeError:
                payload = {}
            events.append({"event_id": _text(raw_id), "seq": _text(raw_id), "type": fields.get("type", ""), "payload": payload})
        return events

    async def close(self) -> None:
        clients = list(self._loop_clients.values())
        if self._client is not None:
            clients.append(self._client)
        self._loop_clients.clear()
        self._client = None
        for client in clients:
            close = getattr(client, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    # A client may belong to an event loop that has already
                    # been closed (common in short-lived test loops).
                    continue


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def create_sandbox_event_store() -> SandboxEventStore | RedisSandboxEventStore:
    """Select durable Redis Streams whenever the deployment configured Redis."""
    redis_url = settings.NLP_AGENT_REDIS_URL.strip()
    if not redis_url:
        return SandboxEventStore()
    from redis.asyncio import Redis

    return RedisSandboxEventStore(
        client_factory=lambda: Redis.from_url(redis_url, decode_responses=True),
        retention_seconds=settings.NLP_AGENT_SANDBOX_EVENT_RETENTION_S,
        max_events=settings.NLP_AGENT_SANDBOX_EVENT_MAXLEN,
        fault_injector=SandboxFaultInjector.from_env(),
    )


default_sandbox_event_store = create_sandbox_event_store()
