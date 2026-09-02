"""Redis transport contracts for independently running turn workers."""

from __future__ import annotations

import json
import inspect
import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from core.learning import ExerciseState, LearningContext, LearningProgress, TeachingMaterials
from core.session_context import SessionContext
from gateway.dispatch import ExecutionAuthorizationContext, TurnTask
from gateway.contracts import GatewayEvent
from gateway.events import GatewayEventBroker


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RedisTransportConfig:
    url: str = "redis://127.0.0.1:6379/0"
    task_stream: str = "nlp-agent:turns"
    task_group: str = "nlp-agent-workers"
    event_channel: str = "nlp-agent:events"
    control_channel: str = "nlp-agent:control"
    authorization_channel: str = "nlp-agent:authorization"
    quota_snapshot_channel: str = "nlp-agent:quota-snapshot"
    reclaim_idle_ms: int = 60_000
    cancel_key_prefix: str = "nlp-agent:cancel:"
    cancel_ttl_s: int = 604_800
    dead_letter_stream: str = "nlp-agent:turns:dead"


class RedisTurnDispatcher:
    """Web-side task port backed by Redis Streams and a cancellation channel."""

    def __init__(self, redis: Any, config: RedisTransportConfig, *, owns_client: bool = False) -> None:
        self._redis = redis
        self.config = config
        self._owns_client = owns_client
        self._active: set[str] = set()

    @classmethod
    def from_config(cls, config: RedisTransportConfig) -> "RedisTurnDispatcher":
        from redis.asyncio import Redis

        return cls(Redis.from_url(config.url, decode_responses=True), config, owns_client=True)

    async def submit(self, task: TurnTask) -> None:
        await self._redis.xadd(self.config.task_stream, {"payload": TurnTaskCodec.dumps(task)})
        self._active.add(task.turn_id)

    async def cancel(self, turn_id: str) -> None:
        await self._redis.set(
            f"{self.config.cancel_key_prefix}{turn_id}",
            "1",
            ex=self.config.cancel_ttl_s,
        )
        payload = json.dumps({"type": "turn.cancel", "turn_id": turn_id}, separators=(",", ":"))
        await self._redis.publish(self.config.control_channel, payload)
        self._active.discard(turn_id)

    async def inject(self, turn_id: str, content: str) -> None:
        payload = json.dumps(
            {"type": "turn.inject", "turn_id": turn_id, "content": content},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        await self._redis.publish(self.config.control_channel, payload)

    async def close(self, *, force: bool = False, grace_s: float = 0) -> None:
        self._active.clear()
        if self._owns_client:
            await self._redis.aclose()

    def active_count(self) -> int:
        return len(self._active)

    def observe(self, event: GatewayEvent) -> None:
        if event.type.value in {"turn.completed", "turn.failed", "turn.cancelled"}:
            self._active.discard(event.turn_id)

    @property
    def client(self) -> Any:
        return self._redis


class RedisWorkerRuntime:
    """Independent Worker consumer for the Redis turn stream."""

    def __init__(
        self,
        redis: Any,
        config: RedisTransportConfig,
        execute: Any,
        *,
        consumer_name: str,
        inject: Any = None,
        cancel_pending: Any = None,
        is_terminal: Any = None,
        reclaim_pending: bool = True,
    ) -> None:
        self._redis = redis
        self.config = config
        self._execute = execute
        self.consumer_name = consumer_name
        self._inject = inject
        self._cancel_pending = cancel_pending
        self._is_terminal = is_terminal
        self._reclaim_pending = reclaim_pending
        self._group_ready = False
        self._closed = False
        self._active: dict[str, asyncio.Task[Any]] = {}
        self._active_turns: dict[str, TurnTask] = {}
        self._command_cancelled: set[str] = set()

    async def _ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            await self._redis.xgroup_create(
                self.config.task_stream, self.config.task_group, id="0", mkstream=True
            )
        except Exception as error:
            if "BUSYGROUP" not in str(error):
                raise
        self._group_ready = True

    async def run_once(self, *, block_ms: int = 5000) -> int:
        await self._ensure_group()
        batches = []
        if self._reclaim_pending and hasattr(self._redis, "xautoclaim"):
            claimed = await self._redis.xautoclaim(
                self.config.task_stream,
                self.config.task_group,
                self.consumer_name,
                self.config.reclaim_idle_ms,
                "0-0",
                count=1,
            )
            if len(claimed) > 1 and claimed[1]:
                batches = [(self.config.task_stream, claimed[1])]
        if not batches:
            batches = await self._redis.xreadgroup(
                self.config.task_group,
                self.consumer_name,
                {self.config.task_stream: ">"},
                count=1,
                block=block_ms,
            )
        processed = 0
        for _stream, messages in batches:
            for message_id, fields in messages:
                try:
                    task = TurnTaskCodec.loads(fields["payload"])
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                    await self._dead_letter(message_id, fields, error)
                    await self._ack(message_id)
                    processed += 1
                    continue
                if await self._call_predicate(self._is_terminal, task):
                    await self._ack(message_id)
                    processed += 1
                    continue
                if await self._redis.get(
                    f"{self.config.cancel_key_prefix}{task.turn_id}"
                ):
                    if self._cancel_pending is not None:
                        await self._call(self._cancel_pending, task)
                    await self._ack(message_id)
                    processed += 1
                    continue
                start_gate = asyncio.Event()
                future = asyncio.create_task(self._execute_after(start_gate, task))
                heartbeat = asyncio.create_task(
                    self._heartbeat(message_id, task.turn_id)
                )
                self._active[task.turn_id] = future
                self._active_turns[task.turn_id] = task
                try:
                    if await self._redis.get(
                        f"{self.config.cancel_key_prefix}{task.turn_id}"
                    ):
                        future.cancel()
                        await asyncio.gather(future, return_exceptions=True)
                        if self._cancel_pending is not None:
                            await self._call(self._cancel_pending, task)
                    else:
                        start_gate.set()
                        await future
                except asyncio.CancelledError:
                    if task.turn_id not in self._command_cancelled:
                        raise
                    if self._cancel_pending is not None:
                        await self._call(self._cancel_pending, task)
                finally:
                    if not future.done():
                        future.cancel()
                        await asyncio.gather(future, return_exceptions=True)
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
                    self._active.pop(task.turn_id, None)
                    self._active_turns.pop(task.turn_id, None)
                    self._command_cancelled.discard(task.turn_id)
                await self._ack(message_id)
                processed += 1
        return processed

    async def _ack(self, message_id: str) -> None:
        await self._redis.xack(
            self.config.task_stream, self.config.task_group, message_id
        )

    async def _dead_letter(
        self, message_id: str, fields: dict[str, Any], error: Exception
    ) -> None:
        await self._redis.xadd(
            self.config.dead_letter_stream,
            {
                "source_stream": self.config.task_stream,
                "source_message_id": message_id,
                "payload": str(fields.get("payload") or ""),
                "error_kind": type(error).__name__,
                "error_message": str(error)[:500],
            },
        )

    async def _heartbeat(self, message_id: str, turn_id: str) -> None:
        interval_s = max(0.01, self.config.reclaim_idle_ms / 3000)
        while True:
            await asyncio.sleep(interval_s)
            try:
                await self._redis.xclaim(
                    self.config.task_stream,
                    self.config.task_group,
                    self.consumer_name,
                    0,
                    [message_id],
                    justid=True,
                )
                if await self._redis.get(
                    f"{self.config.cancel_key_prefix}{turn_id}"
                ):
                    self.cancel_active(turn_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Redis turn lease heartbeat failed; retrying",
                    exc_info=True,
                )

    async def _execute_after(self, start_gate: asyncio.Event, task: TurnTask) -> None:
        await start_gate.wait()
        result = self._execute(task)
        if inspect.isawaitable(result):
            await result

    @staticmethod
    async def _call(callback: Any, task: TurnTask) -> Any:
        result = callback(task)
        return await result if inspect.isawaitable(result) else result

    @classmethod
    async def _call_predicate(cls, callback: Any, task: TurnTask) -> bool:
        return bool(await cls._call(callback, task)) if callback is not None else False

    def cancel_active(self, turn_id: str) -> bool:
        task = self._active.get(turn_id)
        if task is None or task.done():
            return False
        self._command_cancelled.add(turn_id)
        task.cancel()
        return True

    async def run_forever(self) -> None:
        control = asyncio.create_task(self._listen_control())
        try:
            while not self._closed:
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Redis Worker consume iteration failed; retrying")
                    await asyncio.sleep(1)
        finally:
            control.cancel()
            await asyncio.gather(control, return_exceptions=True)

    async def _listen_control(self) -> None:
        while not self._closed:
            pubsub = self._redis.pubsub()
            try:
                await pubsub.subscribe(self.config.control_channel)
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    try:
                        command = json.loads(message["data"])
                        if command.get("type") == "turn.cancel":
                            self.cancel_active(str(command.get("turn_id")))
                        elif (
                            command.get("type") == "turn.inject"
                            and self._inject is not None
                        ):
                            active = self._active_turns.get(
                                str(command.get("turn_id"))
                            )
                            if active is not None:
                                result = self._inject(
                                    active.context,
                                    str(command.get("content") or ""),
                                )
                                if inspect.isawaitable(result):
                                    await result
                    except (TypeError, ValueError, json.JSONDecodeError):
                        logger.warning("Ignoring malformed Redis control message")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Redis control subscription failed; reconnecting", exc_info=True)
            finally:
                try:
                    await pubsub.aclose()
                except Exception:
                    logger.warning("Failed to close Redis control subscription", exc_info=True)
            if not self._closed:
                await asyncio.sleep(1)

    async def close(self) -> None:
        self._closed = True
        for task in self._active.values():
            task.cancel()
        if self._active:
            await asyncio.gather(*self._active.values(), return_exceptions=True)


class RedisEventPublisher:
    """Worker-side notification adapter; durable storage remains authoritative."""

    def __init__(self, redis: Any, config: RedisTransportConfig) -> None:
        self._redis = redis
        self.config = config

    async def publish(self, event: GatewayEvent) -> bool:
        try:
            await self._redis.publish(self.config.event_channel, event.model_dump_json())
        except Exception:
            logger.warning(
                "Redis event notification failed; clients can recover from durable events",
                exc_info=True,
            )
            return False
        return True


class RedisEventBridge:
    """Web-side Pub/Sub listener feeding existing in-process subscriptions."""

    def __init__(self, redis: Any, config: RedisTransportConfig, broker: GatewayEventBroker, *, observe: Any = None) -> None:
        self._redis = redis
        self.config = config
        self._broker = broker
        self._observe = observe
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._listen(), name="redis-event-bridge")

    async def _listen(self) -> None:
        recovering = False
        while True:
            pubsub = self._redis.pubsub()
            try:
                await pubsub.subscribe(self.config.event_channel)
                if recovering:
                    # Connections created during the outage must replay the
                    # durable window once Redis delivery is live again.
                    self._broker.interrupt_all()
                    recovering = False
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    try:
                        event = GatewayEvent.model_validate_json(message["data"])
                    except (TypeError, ValueError):
                        logger.warning("Ignoring malformed Redis gateway event")
                        continue
                    if self._observe is not None:
                        self._observe(event)
                    self._broker.publish(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Redis event subscription failed; reconnecting", exc_info=True)
                self._broker.interrupt_all()
                recovering = True
            finally:
                try:
                    await pubsub.aclose()
                except Exception:
                    logger.warning("Failed to close Redis event subscription", exc_info=True)
            await asyncio.sleep(1)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None


class TurnTaskCodec:
    """Stable JSON representation shared by the Web process and Workers."""

    VERSION = 1

    @staticmethod
    def dumps(task: TurnTask) -> str:
        return json.dumps(
            {
                "version": TurnTaskCodec.VERSION,
                "context": task.context.model_dump(mode="json"),
                "turn_id": task.turn_id,
                "content": task.content,
                "learning_context": task.learning_context.model_dump(mode="json") if task.learning_context else None,
                "learning_progress": task.learning_progress.model_dump(mode="json") if task.learning_progress else None,
                "exercise_state": task.exercise_state.model_dump(mode="json") if task.exercise_state else None,
                "teaching_materials": task.teaching_materials.model_dump(mode="json") if task.teaching_materials else None,
                "guided_session_id": task.guided_session_id,
                "exercise_session_id": task.exercise_session_id,
                  "model_profile": task.model_profile,
                  "reservation_id": task.reservation_id,
                "authorization": (
                    {
                        "submitter_user_id": task.authorization.submitter_user_id,
                        "workspace_id": task.authorization.workspace_id,
                        "authorization_version": task.authorization.authorization_version,
                    }
                    if task.authorization else None
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def loads(payload: str) -> TurnTask:
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError("turn task payload must be a JSON object")
        if value.get("version") != TurnTaskCodec.VERSION:
            raise ValueError(f"unsupported turn task version: {value.get('version')!r}")
        return TurnTask(
            context=SessionContext.model_validate(value["context"]),
            turn_id=str(value["turn_id"]),
            content=str(value["content"]),
            learning_context=LearningContext.model_validate(value["learning_context"]) if value["learning_context"] else None,
            learning_progress=LearningProgress.model_validate(value["learning_progress"]) if value["learning_progress"] else None,
            exercise_state=ExerciseState.model_validate(value["exercise_state"]) if value["exercise_state"] else None,
            teaching_materials=TeachingMaterials.model_validate(value["teaching_materials"]) if value["teaching_materials"] else TeachingMaterials(),
            guided_session_id=value.get("guided_session_id"),
            exercise_session_id=value.get("exercise_session_id"),
              model_profile=value.get("model_profile"),
              reservation_id=value.get("reservation_id"),
            authorization=(
                ExecutionAuthorizationContext(**value["authorization"])
                if value.get("authorization") else None
            ),
        )
