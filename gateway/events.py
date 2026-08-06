"""In-process live delivery backed by the durable Gateway event log."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from gateway.contracts import GatewayEvent
from security.prompt_guard import get_prompt_guard

@dataclass(slots=True)
class _Subscription:
    queue: asyncio.Queue[GatewayEvent]
    turn_id: str | None
    session_id: str | None


class GatewayEventSubscription:
    """An authenticated live subscription whose ownership stays in the Gateway."""

    def __init__(
        self,
        broker: "GatewayEventBroker",
        subscription_id: int,
        queue: asyncio.Queue[GatewayEvent],
    ) -> None:
        self._broker = broker
        self._subscription_id = subscription_id
        self._queue = queue
        self._closed = False

    def __aiter__(self) -> "GatewayEventSubscription":
        return self

    async def __anext__(self) -> GatewayEvent:
        if self._closed:
            raise StopAsyncIteration
        return await self._queue.get()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._broker.unsubscribe(self._subscription_id)


class GatewayEventBroker:
    def __init__(self) -> None:
        self._subscriptions: dict[int, _Subscription] = {}
        self._next_id = 1

    def subscribe(
        self,
        *,
        turn_id: str | None = None,
        session_id: str | None = None,
        maxsize: int = 500,
    ) -> tuple[int, asyncio.Queue[GatewayEvent]]:
        if turn_id is None and session_id is None:
            raise ValueError("turn_id or session_id is required")
        queue: asyncio.Queue[GatewayEvent] = asyncio.Queue(maxsize=maxsize)
        subscription_id = self._next_id
        self._next_id += 1
        self._subscriptions[subscription_id] = _Subscription(queue, turn_id, session_id)
        return subscription_id, queue

    def unsubscribe(self, subscription_id: int) -> None:
        self._subscriptions.pop(subscription_id, None)

    def open_subscription(
        self,
        *,
        turn_id: str | None = None,
        session_id: str | None = None,
        maxsize: int = 500,
    ) -> GatewayEventSubscription:
        subscription_id, queue = self.subscribe(
            turn_id=turn_id,
            session_id=session_id,
            maxsize=maxsize,
        )
        return GatewayEventSubscription(self, subscription_id, queue)

    def publish(self, event: GatewayEvent) -> int:
        dropped = 0
        for subscription in tuple(self._subscriptions.values()):
            if subscription.turn_id is not None and subscription.turn_id != event.turn_id:
                continue
            if subscription.session_id is not None and subscription.session_id != event.session_id:
                continue
            try:
                subscription.queue.put_nowait(event)
            except asyncio.QueueFull:
                # Keep the newest event (especially terminal state). Consumers
                # detect the sequence gap and recover the dropped rows from SQLite.
                try:
                    subscription.queue.get_nowait()
                    subscription.queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
                dropped += 1
        return dropped

    @property
    def subscriber_count(self) -> int:
        return len(self._subscriptions)


class EventEmitter:
    async def send_tool_result(self, tool_name: str, result_text: str):
        # 检测工具返回内容中的间接注入
        prompt_guard = get_prompt_guard()
        is_safe, threat, sanitized = prompt_guard.scan_tool_result(tool_name, result_text)

        if not is_safe:
            # 记录并阻止该事件发送，或发送脱敏后的内容
            sanitized = f"[工具 {tool_name} 返回内容被安全策略拦截]"

        # 推送 sanitized 内容到前端
        await self.push_event("tool_result", {"tool": tool_name, "output": sanitized})