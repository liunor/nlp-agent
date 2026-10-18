"""MySQL-authoritative outbox, Turn lease fencing and tool-operation idempotency."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from gateway.execution_fence import LostTurnClaimError

from server.infrastructure.mysql.models import (
    ConversationModel,
    DeadLetterModel,
    OutboxMessageModel,
    ToolCallModel,
    TurnCancellationModel,
    TurnEventModel,
    TurnModel,
)
from server.storage.policy import StorageBucket
from server.storage.quota import AsyncStorageQuota

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    # MySQL DATETIME carries naive UTC values, including values loaded from
    # aiomysql. Keep lease comparisons in that same convention.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8"))


class TurnCancellationRequested(RuntimeError):
    """The durable turn state says this delivery must be finalized as cancelled."""


@dataclass(frozen=True)
class CancelledTurnClaim:
    """Explicit claim outcome for the cancellation race at the worker boundary."""

    reason: str = "durable_cancellation"


class TurnReliabilityService:
    async def enqueue(self, session: AsyncSession, *, topic: str, payload: dict[str, Any]) -> OutboxMessageModel:
        message = OutboxMessageModel(id=str(uuid.uuid4()), topic=topic, payload_json=payload)
        session.add(message)
        await session.flush()
        return message

    async def quota_reservation_for_turn(
        self, session: AsyncSession, *, turn_id: str
    ) -> str | None:
        reservation_id = await session.scalar(
            select(TurnModel.quota_reservation_id).where(TurnModel.id == turn_id)
        )
        return str(reservation_id) if reservation_id else None

    async def claim_turn(
        self,
        session: AsyncSession,
        *,
        turn_id: str,
        worker_id: str,
        lease_s: int,
        user_id: str | None = None,
        workspace_id: str | None = None,
    ) -> int | CancelledTurnClaim | None:
        statement = (
            select(TurnModel)
            .join(ConversationModel, ConversationModel.id == TurnModel.conversation_id)
            .where(TurnModel.id == turn_id)
        )
        if user_id is not None:
            statement = statement.where(
                TurnModel.user_id == user_id,
                ConversationModel.owner_user_id == user_id,
            )
        if workspace_id is not None:
            statement = statement.where(
                TurnModel.workspace_id == workspace_id,
                ConversationModel.workspace_id == workspace_id,
            )
        turn = await session.scalar(statement.with_for_update())
        now = utc_now()
        if turn is None:
            return None
        cancellation = await session.scalar(
            select(TurnCancellationModel).where(
                TurnCancellationModel.turn_id == turn.id
            )
        )
        if isinstance(cancellation, TurnCancellationModel):
            if turn.status in {"accepted", "running"}:
                turn.status = "cancelled"
                turn.completed_at = now
                turn.claimed_by = None
                turn.lease_expires_at = None
                await session.flush()
            return CancelledTurnClaim()
        if turn.status != "accepted" and not (
            turn.status == "running"
            and turn.lease_expires_at
            and turn.lease_expires_at < now
        ):
            return None
        turn.status = "running"
        turn.claim_generation += 1
        turn.claimed_by = worker_id
        turn.heartbeat_at = now
        turn.lease_expires_at = now + timedelta(seconds=lease_s)
        await session.flush()
        return turn.claim_generation

    async def heartbeat(self, session: AsyncSession, *, turn_id: str, generation: int, worker_id: str, lease_s: int) -> bool:
        turn = await session.scalar(select(TurnModel).where(
            TurnModel.id == turn_id,
            TurnModel.claim_generation == generation,
            TurnModel.claimed_by == worker_id,
            TurnModel.lease_expires_at > func.utc_timestamp(6),
        ).with_for_update())
        if turn is None or turn.claim_generation != generation or turn.claimed_by != worker_id or turn.lease_expires_at is None or turn.lease_expires_at <= utc_now():
            raise LostTurnClaimError(turn_id)
        # A cancellation request racing the terminal commit must never turn a
        # completed/failed result into a second, contradictory terminal state.
        if turn.status == "cancelled":
            return False
        if turn.status in {"completed", "failed", "interrupted"}:
            return True
        cancellation = await session.scalar(
            select(TurnCancellationModel).where(
                TurnCancellationModel.turn_id == turn_id
            )
        )
        if isinstance(cancellation, TurnCancellationModel):
            turn.status = "cancelled"
            turn.completed_at = utc_now()
            turn.claimed_by = None
            turn.lease_expires_at = None
            await session.flush()
            return False
        if turn.status != "running":
            raise LostTurnClaimError(turn_id)
        now = utc_now()
        turn.heartbeat_at = now
        turn.lease_expires_at = now + timedelta(seconds=lease_s)
        await session.flush()
        return True

    async def recover_stuck_turns(self, session: AsyncSession, *, max_retries: int = 3) -> list[str]:
        now = utc_now()
        recoverable = or_(
            TurnModel.lease_expires_at < now,
            # Older recovery code could leave this impossible running state.
            # Treat it as abandoned so deploying the fix repairs existing rows.
            TurnModel.claimed_by.is_(None) & TurnModel.lease_expires_at.is_(None),
        )
        turns = (
            await session.scalars(
                select(TurnModel)
                .where(TurnModel.status == "running", recoverable)
                .with_for_update(skip_locked=True)
            )
        ).all()
        recovered: list[str] = []
        for turn in turns:
            if turn.claim_generation >= max_retries * 2:
                turn.status = "failed"
                turn.error_kind = "turn_lease_recovery_exhausted"
                turn.error_message = "turn lease recovery limit exceeded"
                turn.completed_at = now
                turn.claimed_by = None
                turn.heartbeat_at = None
                turn.lease_expires_at = None
                session.add(DeadLetterModel(id=str(uuid.uuid4()), turn_id=turn.id, outbox_id=None, reason="turn lease recovery limit exceeded", payload_json={"generation": turn.claim_generation}))
                continue
            turn.claim_generation += 1  # recovery invalidates the old owner before a new Worker claim.
            # Recovery must return the turn to a state claim_turn can acquire.
            # ``running`` with no owner and no lease is otherwise a permanent
            # zombie because it is neither fresh nor detectably expired.
            turn.status = "accepted"
            turn.claimed_by = None
            turn.heartbeat_at = None
            turn.lease_expires_at = None
            quota = AsyncStorageQuota(session, owner_user_id=turn.user_id, roles=None)
            reservation = await quota.reserve(StorageBucket.CORE, 256, resource_type="turn_event", resource_key=f"{turn.id}:handover:{turn.claim_generation}")
            session.add(TurnEventModel(id=str(uuid.uuid4()), turn_id=turn.id, sequence=(await self._next_sequence(session, turn.id)), claim_generation=turn.claim_generation, event_type="turn.handover", payload_json={"reason": "lease_expired"}))
            await quota.finalize(reservation, reconcile=True)
            original = await session.scalar(
                select(OutboxMessageModel.payload_json)
                .where(
                    OutboxMessageModel.topic == "turn.dispatch",
                    OutboxMessageModel.payload_json["turn_id"].as_string() == turn.id,
                )
                .order_by(OutboxMessageModel.created_at.desc())
                .limit(1)
            )
            if not isinstance(original, dict) or not isinstance(original.get("task"), str):
                raise ValueError(f"turn {turn.id} has no recoverable dispatch task")
            await self.enqueue(session, topic="turn.dispatch", payload=original)
            recovered.append(turn.id)
        await session.flush()
        return recovered

    async def append_event(self, session: AsyncSession, *, turn_id: str, generation: int, event_type: str, payload: dict[str, Any]) -> TurnEventModel:
        turn = await session.scalar(select(TurnModel).where(TurnModel.id == turn_id).with_for_update())
        if turn is None or turn.claim_generation != generation:
            raise LostTurnClaimError(turn_id)
        quota = AsyncStorageQuota(session, owner_user_id=turn.user_id, roles=None)
        reservation = await quota.reserve(StorageBucket.CORE, len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")) + 256, resource_type="turn_event", resource_key=f"{turn_id}:{event_type}")
        event = TurnEventModel(id=str(uuid.uuid4()), turn_id=turn_id, sequence=await self._next_sequence(session, turn_id), claim_generation=generation, event_type=event_type, payload_json=payload)
        session.add(event)
        await session.flush()
        await quota.finalize(reservation, reconcile=True)
        return event

    async def record_operation(self, session: AsyncSession, *, turn_id: str, generation: int, operation_id: str, tool_name: str, request: dict[str, Any]) -> ToolCallModel:
        operation = await session.scalar(select(ToolCallModel).where(ToolCallModel.turn_id == turn_id, ToolCallModel.operation_id == operation_id).with_for_update())
        if operation is not None:
            return operation
        turn = await session.scalar(select(TurnModel).where(TurnModel.id == turn_id).with_for_update())
        if turn is None or turn.claim_generation != generation:
            raise LostTurnClaimError(turn_id)
        quota = AsyncStorageQuota(session, owner_user_id=turn.user_id, roles=None)
        reservation = await quota.reserve(
            StorageBucket.CORE,
            _json_bytes(request) + len(tool_name.encode("utf-8")) + 256,
            resource_type="tool_call",
            resource_key=f"{turn_id}:{operation_id}:request",
        )
        operation = ToolCallModel(id=str(uuid.uuid4()), turn_id=turn_id, operation_id=operation_id, claim_generation=generation, tool_name=tool_name, idempotency_key=f"{turn_id}:{operation_id}", request_json=request)
        try:
            session.add(operation)
            await session.flush()
            await quota.finalize(reservation, reconcile=True)
            return operation
        except Exception:
            await quota.release(reservation)
            raise

    async def complete_operation(self, session: AsyncSession, *, turn_id: str, generation: int, operation_id: str, result: dict[str, Any]) -> ToolCallModel:
        turn = await session.scalar(select(TurnModel).where(TurnModel.id == turn_id).with_for_update())
        operation = await session.scalar(select(ToolCallModel).where(ToolCallModel.turn_id == turn_id, ToolCallModel.operation_id == operation_id).with_for_update())
        if turn is None or operation is None or turn.claim_generation != generation or operation.claim_generation != generation:
            raise LostTurnClaimError(turn_id)
        old_result = operation.result_json
        # Reserve at least one byte even when a result replaces an equally
        # small/empty value, so finalize still performs the measured-usage
        # check instead of allowing a zero-byte quota bypass.
        result_delta = max(1, _json_bytes(result) - _json_bytes(old_result or {}))
        quota = AsyncStorageQuota(session, owner_user_id=turn.user_id, roles=None)
        reservation = await quota.reserve(
            StorageBucket.CORE,
            result_delta,
            resource_type="tool_call",
            resource_key=f"{turn_id}:{operation_id}:result",
        )
        operation.status = "succeeded"
        operation.result_json = result
        try:
            await session.flush()
            await quota.finalize(reservation, reconcile=True)
            return operation
        except Exception:
            await quota.release(reservation)
            operation.result_json = old_result
            raise

    async def _next_sequence(self, session: AsyncSession, turn_id: str) -> int:
        events = (await session.scalars(select(TurnEventModel.sequence).where(TurnEventModel.turn_id == turn_id).order_by(TurnEventModel.sequence.desc()).limit(1))).first()
        return int(events or 0) + 1


class OutboxRelay:
    """Publish known topics without holding MySQL locks across Redis I/O.

    Only encoded Turn tasks belong in ``stream``. Sandbox lease notifications
    have their own bounded stream; lease state and Manager reconciliation in
    MySQL remain authoritative, independently of notification delivery.
    """

    def __init__(self, redis: Any, *, stream: str, relay_id: str, authorization_channel: str = "nlp-agent:authorization", lock_s: int = 30) -> None:
        self._redis, self._stream, self._relay_id, self._authorization_channel, self._lock_s = redis, stream, relay_id, authorization_channel, lock_s
        self._sandbox_stream = f"{stream}:sandbox-events"

    async def _claim_batch(self, session: AsyncSession, *, limit: int) -> list["_ClaimedOutboxMessage"]:
        now = utc_now()
        rows = (await session.scalars(select(OutboxMessageModel).where(OutboxMessageModel.status.in_(("pending", "retry", "publishing")), OutboxMessageModel.available_at <= now, or_(OutboxMessageModel.locked_until.is_(None), OutboxMessageModel.locked_until < now)).order_by(OutboxMessageModel.available_at, OutboxMessageModel.id).limit(limit).with_for_update(skip_locked=True))).all()
        claimed: list[_ClaimedOutboxMessage] = []
        for row in rows:
            row.status, row.locked_by, row.locked_until, row.attempts = "publishing", self._relay_id, now + timedelta(seconds=self._lock_s), row.attempts + 1
            claimed.append(_ClaimedOutboxMessage(row.id, row.topic, row.payload_json, row.attempts))
        await session.flush()
        return claimed

    async def _publish(self, message: "_ClaimedOutboxMessage") -> str:
        if message.topic == "authorization.changed":
            payload = json.dumps(message.payload_json, ensure_ascii=False)
            return str(await self._redis.publish(self._authorization_channel, payload))
        if message.topic == "turn.dispatch":
            task_payload = message.payload_json.get("task")
            if not isinstance(task_payload, str):
                raise ValueError("turn.dispatch outbox payload requires an encoded task")
            return str(await self._redis.xadd(self._stream, {"payload": task_payload}))
        if message.topic in {"sandbox.lease.released", "sandbox.lease.revoked"}:
            # These are lifecycle notifications, not executable Turn tasks or
            # user-wide authorization invalidations. Keep their source ID for
            # correlation/deduplication after an at-least-once relay retry.
            fields = {
                "outbox_id": message.id,
                "topic": message.topic,
                "payload": json.dumps(message.payload_json, ensure_ascii=False),
            }
            return str(await self._redis.xadd(
                self._sandbox_stream, fields, maxlen=10_000, approximate=True,
            ))
        # publish_batch retains the durable row as retry on failure. A new or
        # misspelled topic must not silently become a malformed Worker task.
        raise ValueError(f"unsupported outbox topic: {message.topic}")

    async def _finalize_batch(
        self,
        session: AsyncSession,
        results: list[tuple["_ClaimedOutboxMessage", str | None]],
    ) -> int:
        published = 0
        for message, redis_id in results:
            row = await session.scalar(
                select(OutboxMessageModel)
                .where(
                    OutboxMessageModel.id == message.id,
                    OutboxMessageModel.status == "publishing",
                    OutboxMessageModel.locked_by == self._relay_id,
                )
                .with_for_update()
            )
            if row is None:
                # A newer relay owns this claim (or the row was already handled).
                continue
            if redis_id is not None:
                row.status = "published"
                row.redis_message_id = redis_id
                row.published_at = utc_now()
                row.locked_by = None
                row.locked_until = None
                published += 1
                continue
            delay_s = min(30, 2 ** min(message.attempts, 5))
            row.status = "retry"
            row.available_at = utc_now() + timedelta(seconds=delay_s)
            row.locked_by = None
            row.locked_until = None
        await session.flush()
        return published

    async def publish_batch(self, uow_factory: Any, *, limit: int = 100) -> int:
        """Claim, publish outside transactions, then durably finalize outcomes.

        A process crash after Redis accepts a message but before finalization can
        cause an at-least-once duplicate after the claim lease expires. Consumers
        must continue to use Turn fencing/idempotency.
        """
        async with uow_factory.begin() as unit_of_work:
            if unit_of_work.session is None:
                raise RuntimeError("outbox relay unit of work has no session")
            claimed = await self._claim_batch(unit_of_work.session, limit=limit)
            await unit_of_work.commit()

        results: list[tuple[_ClaimedOutboxMessage, str | None]] = []
        for message in claimed:
            try:
                results.append((message, await self._publish(message)))
            except Exception:
                logger.exception("outbox publish failed: id=%s topic=%s", message.id, message.topic)
                results.append((message, None))

        if not results:
            return 0
        async with uow_factory.begin() as unit_of_work:
            if unit_of_work.session is None:
                raise RuntimeError("outbox relay unit of work has no session")
            published = await self._finalize_batch(unit_of_work.session, results)
            await unit_of_work.commit()
        return published


@dataclass(frozen=True)
class _ClaimedOutboxMessage:
    id: str
    topic: str
    payload_json: dict[str, Any]
    attempts: int
