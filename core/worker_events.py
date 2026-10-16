"""Typed, deduplicated, session-scoped Worker completion events."""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Awaitable, Callable

from schemas.models import WorkerExecutionResultSpec
from utils.logger import get_logger


logger = get_logger("nlp_agent.worker_events")
SessionNotifier = Callable[[str], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class WorkerCompletedEvent:
    event_id: str
    session_id: str
    worker_id: str
    parent_turn_id: str
    attempt: int
    sequence: int
    created_at: float
    execution: WorkerExecutionResultSpec
    join: bool

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        worker_id: str,
        parent_turn_id: str,
        attempt: int,
        execution: WorkerExecutionResultSpec,
        join: bool,
        sequence: int = 1,
        event_id: str | None = None,
    ) -> "WorkerCompletedEvent":
        return cls(
            event_id=event_id or str(uuid.uuid4()),
            session_id=session_id,
            worker_id=worker_id,
            parent_turn_id=parent_turn_id,
            attempt=attempt,
            sequence=sequence,
            created_at=time.time(),
            execution=execution,
            join=join,
        )


@dataclass(slots=True)
class WorkerEventMetrics:
    published: int = 0
    consumed: int = 0
    duplicate_events: int = 0
    publish_timeouts: int = 0
    queue_depth_peak: int = 0


class WorkerEventBus:
    def __init__(
        self,
        max_events_per_session: int = 100,
        *,
        publish_timeout_s: float = 5.0,
        dedupe_window: int = 1000,
    ) -> None:
        self._max_events_per_session = max_events_per_session
        self._publish_timeout_s = publish_timeout_s
        self._dedupe_window = dedupe_window
        self._queues: dict[str, asyncio.Queue[WorkerCompletedEvent]] = defaultdict(
            lambda: asyncio.Queue(maxsize=self._max_events_per_session)
        )
        self._overflow: dict[str, deque[WorkerCompletedEvent]] = defaultdict(deque)
        # Events removed while another turn is waiting stay available for the
        # matching turn instead of being injected into the wrong Coordinator run.
        self._deferred: dict[str, deque[WorkerCompletedEvent]] = defaultdict(deque)
        self._subscribers: dict[str, tuple[str | None, SessionNotifier]] = {}
        self._seen_ids: dict[str, set[str]] = defaultdict(set)
        self._seen_order: dict[str, deque[str]] = defaultdict(deque)
        self.metrics = WorkerEventMetrics()

    def subscribe(self, notifier: SessionNotifier, session_id: str | None = None) -> str:
        subscription_id = str(uuid.uuid4())
        self._subscribers[subscription_id] = (session_id, notifier)
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> None:
        self._subscribers.pop(subscription_id, None)

    async def publish(self, event: WorkerCompletedEvent) -> bool:
        seen = self._seen_ids[event.session_id]
        if event.event_id in seen:
            self.metrics.duplicate_events += 1
            return False
        queue = self._queues[event.session_id]
        try:
            await asyncio.wait_for(queue.put(event), timeout=self._publish_timeout_s)
        except asyncio.TimeoutError:
            self.metrics.publish_timeouts += 1
            # Never drop a terminal Worker result. The overflow remains ordered
            # behind the already queued events and is drained by the same session consumer.
            self._overflow[event.session_id].append(event)
        self._remember(event.session_id, event.event_id)
        self.metrics.published += 1
        self.metrics.queue_depth_peak = max(self.metrics.queue_depth_peak, queue.qsize())
        self._notify_subscribers(event.session_id)
        return True

    async def get(self, session_id: str, timeout_s: float | None = None) -> WorkerCompletedEvent:
        queue = self._queues[session_id]
        event = await queue.get() if timeout_s is None else await asyncio.wait_for(queue.get(), timeout_s)
        self._refill(session_id)
        self.metrics.consumed += 1
        return event

    async def get_for_turn(
        self,
        session_id: str,
        parent_turn_id: str,
        timeout_s: float | None = None,
        worker_ids: frozenset[str] | None = None,
    ) -> WorkerCompletedEvent:
        """Return one completion for a turn without consuming other turns' events."""
        deferred = self._deferred[session_id]
        for index, event in enumerate(deferred):
            if event.parent_turn_id == parent_turn_id and (
                worker_ids is None or event.worker_id in worker_ids
            ):
                del deferred[index]
                self.metrics.consumed += 1
                return event

        queue = self._queues[session_id]
        loop = asyncio.get_running_loop()
        deadline = None if timeout_s is None else loop.time() + timeout_s
        while True:
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                raise asyncio.TimeoutError
            event = await queue.get() if remaining is None else await asyncio.wait_for(
                queue.get(), remaining
            )
            self._refill(session_id)
            if event.parent_turn_id == parent_turn_id and (
                worker_ids is None or event.worker_id in worker_ids
            ):
                self.metrics.consumed += 1
                return event
            deferred.append(event)

    def drain(self, session_id: str, limit: int = 100) -> list[WorkerCompletedEvent]:
        queue = self._queues[session_id]
        events: list[WorkerCompletedEvent] = []
        deferred = self._deferred[session_id]
        while len(events) < limit and deferred:
            events.append(deferred.popleft())
        while len(events) < limit:
            try:
                events.append(queue.get_nowait())
                self._refill(session_id)
            except asyncio.QueueEmpty:
                break
        overflow = self._overflow[session_id]
        while len(events) < limit and overflow:
            events.append(overflow.popleft())
        self.metrics.consumed += len(events)
        return events

    def drain_for_turn(
        self,
        session_id: str,
        parent_turn_id: str,
        limit: int = 100,
        worker_ids: frozenset[str] | None = None,
    ) -> list[WorkerCompletedEvent]:
        """Drain only one turn while retaining completions for other turns."""
        events: list[WorkerCompletedEvent] = []
        deferred = self._deferred[session_id]
        retained: deque[WorkerCompletedEvent] = deque()
        while deferred:
            event = deferred.popleft()
            if (
                event.parent_turn_id == parent_turn_id
                and (worker_ids is None or event.worker_id in worker_ids)
                and len(events) < limit
            ):
                events.append(event)
            else:
                retained.append(event)
        deferred.extend(retained)

        queue = self._queues[session_id]
        while len(events) < limit:
            try:
                event = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._refill(session_id)
            if event.parent_turn_id == parent_turn_id and (
                worker_ids is None or event.worker_id in worker_ids
            ):
                events.append(event)
            else:
                deferred.append(event)

        # When the main queue was full, publish() puts events into _overflow
        # instead of the queue.  The sync drain path never calls _refill, so
        # overflow must be scanned explicitly; otherwise terminal Worker results
        # that overflowed are invisible to the caller and the turn will hang
        # until its execution timeout fires.
        overflow = self._overflow[session_id]
        retained_overflow: deque[WorkerCompletedEvent] = deque()
        while overflow and len(events) < limit:
            event = overflow.popleft()
            if event.parent_turn_id == parent_turn_id and (
                worker_ids is None or event.worker_id in worker_ids
            ):
                events.append(event)
            else:
                retained_overflow.append(event)
        # Restore events that belong to other turns, preserving FIFO order.
        overflow.extendleft(reversed(retained_overflow))

        self.metrics.consumed += len(events)
        return events

    def has_pending(self, session_id: str) -> bool:
        return (
            not self._queues[session_id].empty()
            or bool(self._overflow[session_id])
            or bool(self._deferred[session_id])
        )

    def metrics_snapshot(self) -> dict[str, int]:
        return {
            "published": self.metrics.published,
            "consumed": self.metrics.consumed,
            "duplicate_events": self.metrics.duplicate_events,
            "publish_timeouts": self.metrics.publish_timeouts,
            "queue_depth_peak": self.metrics.queue_depth_peak,
        }

    def _remember(self, session_id: str, event_id: str) -> None:
        seen = self._seen_ids[session_id]
        order = self._seen_order[session_id]
        seen.add(event_id)
        order.append(event_id)
        while len(order) > self._dedupe_window:
            seen.discard(order.popleft())

    def _refill(self, session_id: str) -> None:
        queue = self._queues[session_id]
        overflow = self._overflow[session_id]
        while overflow and not queue.full():
            queue.put_nowait(overflow.popleft())

    def _notify_subscribers(self, session_id: str) -> None:
        for subscribed_session, notifier in list(self._subscribers.values()):
            if subscribed_session is not None and subscribed_session != session_id:
                continue
            try:
                result = notifier(session_id)
                if inspect.isawaitable(result):
                    task = asyncio.create_task(result)
                    task.add_done_callback(self._log_notifier_error)
            except Exception:
                logger.exception("Worker event subscriber failed", session_id=session_id)

    @staticmethod
    def _log_notifier_error(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error:
            logger.error("Worker event subscriber failed", error=str(error))


global_worker_event_bus = WorkerEventBus()
