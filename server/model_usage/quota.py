"""Transactional token reservations and idempotent Provider usage events."""

from __future__ import annotations

import hashlib
import json
import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession

from core.model_runtime.reporters import UsageEventConflictError
from core.model_runtime.usage import (
    CanonicalTokenUsage,
    InvocationOutcome,
    ModelInvocation,
)
from server.infrastructure.mysql.models import (
    ModelQuotaAccountModel,
    ModelQuotaReservationModel,
    ModelUsageEventModel,
    ModelUsageIntentModel,
    TurnModel,
)
from gateway.contracts import AdmissionLimitError
from gateway.execution_fence import TurnWriteFence
from server.model_usage.journal import UsageJournal


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _DispatchPermit:
    fence: TurnWriteFence
    deadline: float


def _utc_naive(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("model usage timestamps must be timezone-aware")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError("model usage timestamps must use UTC")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _event_payload(
    invocation: ModelInvocation,
    usage: CanonicalTokenUsage,
    outcome: InvocationOutcome,
) -> dict[str, Any]:
    attribution = invocation.attribution
    identity = invocation.identity
    return {
        "operation_id": invocation.operation_id,
        "request_id": attribution.request_id,
        "reservation_id": attribution.reservation_id,
        "user_id": attribution.user_id,
        "workspace_id": attribution.workspace_id,
        "conversation_id": attribution.conversation_id,
        "turn_id": attribution.turn_id,
        "worker_id": attribution.worker_id,
        "parent_operation_id": attribution.parent_operation_id,
        "purpose": attribution.purpose,
        "provider": identity.provider,
        "provider_model": identity.provider_model,
        "model_profile": identity.model_profile,
        "preset": identity.preset,
        "route": identity.route,
        "pricing_key": identity.pricing_key,
        "pricing_version": getattr(identity, "pricing_version", None),
        "charge_units": None,
        "attempt": invocation.attempt,
        "fallback_index": invocation.fallback_index,
        "input_tokens": usage.input_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "cache_write_input_tokens": usage.cache_write_input_tokens,
        "output_tokens": usage.output_tokens,
        "reasoning_output_tokens": usage.reasoning_output_tokens,
        "total_tokens": usage.total_tokens,
        "usage_source": usage.source,
        "provider_response_id": usage.provider_response_id,
        "outcome_status": outcome.status,
        "finish_reason": outcome.finish_reason,
        "error_kind": outcome.error_kind,
        "started_at": invocation.started_at.isoformat(),
        "completed_at": outcome.completed_at.isoformat(),
    }


class DurableQuotaUsageReporter:
    """Persist every model attempt before allowing the caller to continue."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], *, journal_directory: Path | str | None = None, preparation_timeout_s: float = 5.0, downstream_reporter: Any = None) -> None:
        if preparation_timeout_s <= 0:
            raise ValueError("preparation deadline must be positive")
        self._session_factory = session_factory
        self.journal = UsageJournal(journal_directory)
        self.preparation_timeout_s = preparation_timeout_s
        self._dispatch_permits: dict[str, _DispatchPermit] = {}
        self._downstream_reporter = downstream_reporter

    def _token_invocation(self, invocation: ModelInvocation) -> ModelInvocation | None:
        if self._downstream_reporter is None:
            return invocation
        token_id = invocation.attribution.token_reservation_id
        if token_id is None:
            return None  # Existing feature/background calls remain on Credits.
        return invocation.model_copy(update={"attribution": invocation.attribution.model_copy(update={"reservation_id": token_id})})

    async def reserve_feature_usage(self, invocation: ModelInvocation) -> None:
        reserve = getattr(self._downstream_reporter, "reserve_feature_usage", None)
        if reserve is not None:
            await reserve(invocation)

    def set_snapshot_notifier(self, notifier: Any | None) -> None:
        setter = getattr(self._downstream_reporter, "set_snapshot_notifier", None)
        if setter is not None:
            setter(notifier)

    def close(self) -> None:
        close = getattr(self._downstream_reporter, "close", None)
        if close is not None:
            close()

    async def _report_downstream(self, invocation, usage, outcome) -> None:
        if self._downstream_reporter is not None and outcome.error_kind != "provider_not_dispatched":
            await self._downstream_reporter.report(invocation, usage, outcome)

    @staticmethod
    async def _lock_capacity(session: AsyncSession) -> None:
        await session.execute(text(
            "SELECT scope_key FROM nlp_runtime_capacity "
            "WHERE scope_key='global:turns' FOR UPDATE"
        ))

    @staticmethod
    async def _lock_turn(session: AsyncSession, invocation: ModelInvocation):
        if not invocation.attribution.reservation_id:
            return None
        return await session.scalar(select(TurnModel).where(
            TurnModel.id == invocation.attribution.turn_id
        ).with_for_update())

    def check_dispatch(self, invocation: ModelInvocation) -> None:
        """No-await local fence check immediately before Provider dispatch."""
        invocation = self._token_invocation(invocation)
        if invocation is None:
            return
        if invocation.attribution.reservation_id:
            from gateway.execution_fence import LostTurnClaimError, current_turn_write_fence

            fence = current_turn_write_fence()
            permit = self._dispatch_permits.pop(invocation.operation_id, None)
            if (fence is None or permit is None or permit.fence is not fence
                or time.monotonic() >= permit.deadline):
                raise LostTurnClaimError(invocation.attribution.turn_id)
            fence.require_valid(invocation.attribution.turn_id)

    async def prepare(self, invocation: ModelInvocation) -> None:
        """Persist a call intent and atomically extend its Turn's token budget."""
        invocation = self._token_invocation(invocation)
        if invocation is None:
            return
        attr = invocation.attribution
        bound = invocation.identity.context_window_tokens
        output = invocation.identity.max_output_tokens
        if not bound or not output:
            raise ValueError("durable model calls require context and output limits")
        budget = bound + output
        permit = None
        # This deadline covers pool/row-lock acquisition and preparation SQL,
        # independently of Provider timeouts. Exit it before committing: a
        # cancelled preparation rolls back, rather than cancelling a COMMIT
        # whose outcome could be uncertain. COMMIT is not Provider latency either.
        async with self._session_factory.begin() as session, asyncio.timeout(self.preparation_timeout_s):
            turn = await self._lock_turn(session, invocation)
            await self._lock_capacity(session)
            reservation = await self._lock_reservation(session, attr.reservation_id)
            if attr.reservation_id:
                if turn is None or turn.status not in {"accepted", "running"}:
                    raise ValueError("cannot start a model call for a terminal Turn")
                if reservation is None or reservation.user_id != attr.user_id or reservation.turn_id != attr.turn_id:
                    raise ValueError("model call reservation identity mismatch")
                if reservation.status != "reserved":
                    raise ValueError("model call reservation is no longer active")
            existing = await session.get(ModelUsageIntentModel, invocation.operation_id, with_for_update=True)
            if existing is not None:
                raise ValueError("Provider operation already prepared; replay its usage instead")
            await self._ensure_account(session, attr.user_id)
            account = await session.scalar(select(ModelQuotaAccountModel).where(
                ModelQuotaAccountModel.user_id == attr.user_id
            ).with_for_update())
            if account is None:
                raise RuntimeError("model quota account disappeared")
            if attr.reservation_id:
                from gateway.execution_fence import LostTurnClaimError, current_turn_write_fence

                fence = current_turn_write_fence()
                if fence is None:
                    raise LostTurnClaimError(attr.turn_id)
                fence.require_valid(attr.turn_id)
                # Read server time AFTER waiting for every quota lock. The Turn
                # remains locked, so generation/owner cannot change underneath us.
                now = await session.scalar(select(func.utc_timestamp(6)))
                fence.require_valid(attr.turn_id)
                if (turn.status != "running" or turn.claim_generation != fence.generation
                    or turn.claimed_by != fence.worker_id or turn.lease_expires_at is None
                    or turn.lease_expires_at <= now):
                    raise LostTurnClaimError(attr.turn_id)
            if reservation is not None:
                budgets = (await session.scalars(select(ModelUsageIntentModel.reserved_tokens).where(
                    ModelUsageIntentModel.reservation_id == reservation.id,
                    ModelUsageIntentModel.status.in_(["pending", "unknown_usage"]),
                ).with_for_update())).all()
                extra = max(0, sum(budgets) + budget - reservation.reserved_tokens)
                limit = account.token_limit if account.token_limit is not None else account.quota_tokens
                if limit is not None and account.used_tokens + account.reserved_tokens + extra > limit:
                    raise AdmissionLimitError(AdmissionLimitError.MODEL_QUOTA_EXCEEDED)
                # The locking intent read above can also wait. Recheck lease and
                # the shared local fence at the last asynchronous boundary.
                clock_anchor = time.monotonic()
                now = await session.scalar(select(func.utc_timestamp(6)))
                fence.require_valid(attr.turn_id)
                if turn.lease_expires_at <= now:
                    raise LostTurnClaimError(attr.turn_id)
                # Anchor BEFORE the server clock query: network/commit latency
                # shortens this permit, never extends the database lease. A slow
                # COMMIT cannot authorize dispatch after a new owner takes over.
                permit = _DispatchPermit(fence, clock_anchor + (turn.lease_expires_at - now).total_seconds())
                account.reserved_tokens += extra
                reservation.reserved_tokens += extra
                reservation.pending_invocations += 1
            session.add(ModelUsageIntentModel(
                operation_id=invocation.operation_id, reservation_id=attr.reservation_id,
                user_id=attr.user_id, reserved_tokens=budget,
                invocation_json=invocation.model_dump(mode="json"), status="pending",
            ))
        if permit is not None:
            self._dispatch_permits[invocation.operation_id] = permit

    async def replay_pending(self) -> dict[str, int]:
        replayed = failed = 0
        for path in await asyncio.to_thread(self.journal.pending):
            try:
                invocation, usage, outcome = await asyncio.to_thread(self.journal.read, path)
                await self._persist_event(invocation, usage, outcome)
                await self._report_downstream(invocation, usage, outcome)
                await asyncio.to_thread(self.journal.acknowledge, path)
                replayed += 1
            except FileNotFoundError:
                continue  # Another Worker acknowledged this shared-volume fact.
            except Exception:
                failed += 1
                logger.exception("usage outbox replay failed for %s", path.name)
        return {"replayed": replayed, "failed": failed}

    async def report(
        self,
        invocation: ModelInvocation,
        usage: CanonicalTokenUsage,
        outcome: InvocationOutcome,
    ) -> None:
        self._dispatch_permits.pop(invocation.operation_id, None)
        # The exact fact reaches persistent storage before attempting MySQL.
        # A DB failure leaves both this fact and the pre-call intent pending.
        path = await asyncio.to_thread(self.journal.put, invocation, usage, outcome)
        await self._persist_event(invocation, usage, outcome)
        await self._report_downstream(invocation, usage, outcome)
        try:
            await asyncio.to_thread(self.journal.acknowledge, path)
        except OSError:
            # MySQL already committed the charge. Leave the redundant local
            # fact for idempotent replay without failing a successful Turn.
            logger.warning("unable to remove acknowledged usage fact %s", path.name, exc_info=True)

    async def _persist_event(self, invocation: ModelInvocation, usage: CanonicalTokenUsage, outcome: InvocationOutcome) -> None:
        invocation = self._token_invocation(invocation)
        if invocation is None:
            return
        attribution = invocation.attribution
        user_id = attribution.user_id
        payload = _event_payload(invocation, usage, outcome)
        payload_sha256 = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        async with self._session_factory.begin() as session:
            turn = await self._lock_turn(session, invocation)
            await self._lock_capacity(session)
            reservation = await self._lock_reservation(session, attribution.reservation_id)
            if attribution.reservation_id is not None and reservation is None:
                raise ValueError("model usage reservation does not exist")
            if reservation is not None:
                if reservation.user_id != user_id:
                    raise ValueError("model usage reservation belongs to a different user")
                if attribution.turn_id and reservation.turn_id != attribution.turn_id:
                    raise ValueError("model usage reservation belongs to a different Turn")

            await self._ensure_account(session, user_id)
            account = await session.scalar(
                select(ModelQuotaAccountModel)
                .where(ModelQuotaAccountModel.user_id == user_id)
                .with_for_update()
            )
            if account is None:
                raise RuntimeError("model quota account disappeared during usage reporting")

            existing = await session.get(ModelUsageEventModel, invocation.operation_id, with_for_update=True)
            if existing is not None:
                if existing.payload_sha256 != payload_sha256:
                    raise UsageEventConflictError(
                        f"Conflicting usage report for operation_id {invocation.operation_id!r}"
                    )
                return

            intent = await session.get(ModelUsageIntentModel, invocation.operation_id, with_for_update=True)
            if intent is not None and intent.invocation_json != invocation.model_dump(mode="json"):
                raise UsageEventConflictError("usage does not match the prepared Provider operation")
            values = dict(payload)
            values.update(
                provider_response_id=(usage.provider_response_id or "")[:256] or None,
                finish_reason=(outcome.finish_reason or "")[:128] or None,
                error_kind=(outcome.error_kind or "")[:128] or None,
                started_at=_utc_naive(invocation.started_at),
                completed_at=_utc_naive(outcome.completed_at),
                payload_sha256=payload_sha256,
            )
            session.add(ModelUsageEventModel(**values))
            await session.flush()

            if usage.source == "none":
                account.unknown_usage_events += 1
                if reservation is not None:
                    reservation.unknown_usage_events += 1
                    if reservation.status != "reserved":
                        reservation.status = "unknown_usage"
            else:
                account.consumed_tokens += usage.total_tokens
                account.used_tokens += usage.total_tokens
                if reservation is not None:
                    reservation.used_tokens += usage.total_tokens
                    if reservation.status == "reserved":
                        consumed_reserve = min(reservation.reserved_tokens, usage.total_tokens)
                        if account.reserved_tokens < consumed_reserve:
                            raise RuntimeError("model quota reservation counter underflow")
                        account.reserved_tokens -= consumed_reserve
                        reservation.reserved_tokens -= consumed_reserve
                    else:
                        reservation.settled_tokens = reservation.used_tokens

            if intent is not None and intent.status == "pending":
                intent.status = "unknown_usage" if usage.source == "none" else "reported"
                if reservation is not None:
                    if reservation.pending_invocations < 1:
                        raise RuntimeError("model usage pending counter underflow")
                    reservation.pending_invocations -= 1
            if reservation is not None and reservation.status == "reserved" and (
                turn is None or turn.status not in {"accepted", "running"}
            ) and reservation.pending_invocations == 0 and reservation.unknown_usage_events == 0:
                if account.reserved_tokens < reservation.reserved_tokens:
                    raise RuntimeError("model quota reservation counter underflow")
                account.reserved_tokens -= reservation.reserved_tokens
                reservation.status = "settled" if turn is not None else "released"
                reservation.settled_tokens = reservation.used_tokens
                reservation.finalized_at = datetime.now(timezone.utc).replace(tzinfo=None)
                reservation.expires_at = None

    @staticmethod
    async def _ensure_account(session: AsyncSession, user_id: str) -> None:
        statement = mysql_insert(ModelQuotaAccountModel).values(user_id=user_id)
        await session.execute(
            statement.on_duplicate_key_update(user_id=statement.inserted.user_id)
        )

    @staticmethod
    async def _lock_reservation(
        session: AsyncSession, reservation_id: str | None
    ) -> ModelQuotaReservationModel | None:
        if reservation_id is None:
            return None
        return await session.scalar(
            select(ModelQuotaReservationModel)
            .where(ModelQuotaReservationModel.id == reservation_id)
            .with_for_update()
        )
