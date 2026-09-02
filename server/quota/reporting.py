"""Durable Runtime usage Reporter and exact Shadow Credits persistence."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy import create_engine

from core.model_runtime.usage import (
    CanonicalTokenUsage,
    InvocationOutcome,
    ModelInvocation,
    ModelUsageReporter,
)
from core.model_runtime.reporters import UsageEventConflictError
from server.quota.models import PricingRuleModel, UsageEventModel
from server.quota.service import QuotaService
from server.quota.pricing import (
    EstimatedUsageCannotBePricedError,
    PricingCatalog,
    PricingRule,
    UnknownPricingKeyError,
    UnknownUsageCannotBePricedError,
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class DurableModelUsageReporter(ModelUsageReporter):
    """Persist each Runtime Attempt exactly once by ``operation_id``.

    The interface is intentionally the upstream Reporter protocol.  The
    adapter owns pricing-rule loading, Shadow calculation, conflict detection,
    and the short database transaction so Runtime callers remain provider
    neutral and do not need to know the storage schema.
    """

    def __init__(
        self,
        database: str | Engine,
        *,
        quota_service: QuotaService | None = None,
    ) -> None:
        if isinstance(database, str):
            if database.startswith("mysql+aiomysql://"):
                database = database.replace("mysql+aiomysql://", "mysql+pymysql://", 1)
            self._engine = create_engine(database, pool_pre_ping=True)
            self._owns_engine = True
        else:
            self._engine = database
            self._owns_engine = False
        self._quota_service = quota_service

    def set_snapshot_notifier(self, notifier: Any | None) -> None:
        if self._quota_service is not None:
            self._quota_service.set_snapshot_notifier(notifier)

    async def report(
        self,
        invocation: ModelInvocation,
        usage: CanonicalTokenUsage,
        outcome: InvocationOutcome,
    ) -> None:
        await asyncio.to_thread(self._report_sync, invocation, usage, outcome)

    def _report_sync(
        self,
        invocation: ModelInvocation,
        usage: CanonicalTokenUsage,
        outcome: InvocationOutcome,
    ) -> None:
        payload = {
            "invocation": invocation.model_dump(mode="json"),
            "usage": usage.model_dump(mode="json"),
            "outcome": outcome.model_dump(mode="json"),
        }
        usage_values = self._values(invocation, usage, outcome, payload)
        reservation_id_to_notify = invocation.attribution.reservation_id
        try:
            with self._engine.begin() as connection:
                existing = self._existing_event(connection, invocation.operation_id)
                if existing is not None:
                    reservation_id_to_notify = (
                        existing["reservation_id"] or reservation_id_to_notify
                    )
                    existing_payload = existing["raw_usage_json"]
                    current_status = existing["usage_status"]
                    next_status = usage_values["usage_status"]
                    if existing_payload == payload and not (
                        current_status in {"pending", "unavailable"}
                        and next_status in {"exact", "estimated"}
                    ):
                        if (
                            self._quota_service is not None
                            and existing["reservation_id"]
                        ):
                            self._settle_in_transaction(
                                connection,
                                reservation_id=existing["reservation_id"],
                                invocation=invocation,
                                usage_values=usage_values,
                            )
                    elif (
                        current_status in {"pending", "unavailable"}
                        and next_status in {"exact", "estimated"}
                    ):
                        reservation_id = (
                            existing["reservation_id"]
                            or invocation.attribution.reservation_id
                        )
                        if self._quota_service is not None and reservation_id:
                            self._quota_service.reconcile_usage_in_transaction(
                                connection,
                                reservation_id=reservation_id,
                                operation_id=invocation.operation_id,
                                credits_micro=usage_values["credits_micro"] or 0,
                                usage_status=next_status,
                                usage_source=usage.source,
                                pricing_key=usage_values["pricing_key"],
                                pricing_version=usage_values["pricing_version"],
                                now=_utc(outcome.completed_at),
                            )
                        mutable_values = {
                            key: value
                            for key, value in usage_values.items()
                            if key
                            not in {
                                "id",
                                "operation_id",
                                "dedupe_key",
                                "idempotency_key",
                                "created_at",
                            }
                        }
                        connection.execute(
                            update(UsageEventModel)
                            .where(
                                UsageEventModel.operation_id
                                == invocation.operation_id
                            )
                            .values(**mutable_values)
                        )
                    else:
                        self._assert_replay(
                            existing_payload, payload, invocation.operation_id
                        )
                else:
                    connection.execute(insert(UsageEventModel).values(**usage_values))
                    if self._quota_service is not None and invocation.attribution.reservation_id:
                        self._settle_in_transaction(
                            connection,
                            reservation_id=invocation.attribution.reservation_id,
                            invocation=invocation,
                            usage_values=usage_values,
                        )
        except IntegrityError:
            # A concurrent reporter may win the unique operation_id insert.
            # Re-run against the committed winner so a pending -> exact
            # report can take the normal reconciliation path as well.
            with self._engine.connect() as connection:
                existing = self._existing_event(connection, invocation.operation_id)
            if existing is None:
                raise
            return self._report_sync(invocation, usage, outcome)
        if self._quota_service is not None and reservation_id_to_notify:
            self._quota_service.notify_reservation(reservation_id_to_notify)

    def _settle_in_transaction(
        self,
        connection: Connection,
        *,
        reservation_id: str,
        invocation: ModelInvocation,
        usage_values: dict[str, Any],
    ) -> None:
        self._quota_service.settle_usage_in_transaction(
            connection,
            reservation_id=reservation_id,
            operation_id=invocation.operation_id,
            credits_micro=usage_values["credits_micro"] or 0,
            usage_status=usage_values["usage_status"],
            usage_source=usage_values["usage_source"],
            pricing_key=usage_values["pricing_key"],
            pricing_version=usage_values["pricing_version"],
        )

    def _values(
        self,
        invocation: ModelInvocation,
        usage: CanonicalTokenUsage,
        outcome: InvocationOutcome,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        pricing_version, credits_micro, usage_status = self._price(
            invocation, usage, outcome
        )
        attribution = invocation.attribution
        identity = invocation.identity
        started_at = _utc(invocation.started_at)
        occurred_at = _utc(outcome.completed_at)
        return {
            "id": str(uuid.uuid4()),
            "operation_id": invocation.operation_id,
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
            "provider_response_id": usage.provider_response_id,
            "model_profile": identity.model_profile,
            "preset": identity.preset,
            "route": identity.route,
            "pricing_key": identity.pricing_key,
            "attempt": invocation.attempt,
            "fallback_index": invocation.fallback_index,
            "outcome_status": outcome.status,
            "finish_reason": outcome.finish_reason,
            "error_kind": outcome.error_kind,
            "input_tokens": usage.input_tokens,
            "cached_input_tokens": usage.cached_input_tokens,
            "cache_write_input_tokens": usage.cache_write_input_tokens,
            "output_tokens": usage.output_tokens,
            "reasoning_output_tokens": usage.reasoning_output_tokens,
            "total_tokens": usage.total_tokens,
            "usage_source": usage.source,
            "usage_status": usage_status,
            "pricing_version": pricing_version,
            "credits_micro": credits_micro,
            "raw_usage_json": payload,
            "dedupe_key": invocation.operation_id,
            "idempotency_key": invocation.operation_id,
            "started_at": started_at,
            "occurred_at": occurred_at,
            "created_at": datetime.now(timezone.utc),
        }

    def _price(
        self,
        invocation: ModelInvocation,
        usage: CanonicalTokenUsage,
        outcome: InvocationOutcome,
    ) -> tuple[str | None, int | None, str]:
        if usage.source == "none":
            return None, None, "unavailable"
        if usage.semantics in {"cumulative", "delta", "partial"} or outcome.status in {
            "cancelled",
            "interrupted",
        }:
            return None, None, "pending"
        with self._engine.connect() as connection:
            catalog = PricingCatalog(self._pricing_rules(connection, invocation))
        try:
            priced = catalog.price(
                invocation,
                usage,
                outcome,
                allow_estimated=True,
            )
        except (
            EstimatedUsageCannotBePricedError,
            UnknownUsageCannotBePricedError,
            UnknownPricingKeyError,
        ):
            # Missing price configuration remains pending and is never
            # silently converted to zero credits.
            return None, None, "pending"
        return priced.pricing_version, priced.credits_micro, (
            "estimated" if usage.source == "estimated" else "exact"
        )

    @staticmethod
    def _pricing_rules(
        connection: Connection, invocation: ModelInvocation
    ) -> list[PricingRule]:
        if invocation.identity.pricing_key is None:
            return []
        rows = connection.execute(
            select(PricingRuleModel.__table__).where(
                PricingRuleModel.pricing_key == invocation.identity.pricing_key,
                PricingRuleModel.status == "active",
            )
        ).mappings()
        return [
            PricingRule(
                pricing_key=row["pricing_key"],
                version=row["version"],
                effective_from=_utc(row["effective_from"]),
                effective_until=(
                    _utc(row["effective_until"])
                    if row["effective_until"] is not None
                    else None
                ),
                ordinary_input_credits_micro_per_million_tokens=(
                    row["ordinary_input_credits_micro_per_million_tokens"]
                ),
                cached_input_credits_micro_per_million_tokens=(
                    row["cached_input_credits_micro_per_million_tokens"]
                ),
                cache_write_credits_micro_per_million_tokens=(
                    row["cache_write_credits_micro_per_million_tokens"]
                ),
                output_credits_micro_per_million_tokens=(
                    row["output_credits_micro_per_million_tokens"]
                ),
                reasoning_output_credits_micro_per_million_tokens=(
                    row["reasoning_output_credits_micro_per_million_tokens"]
                ),
            )
            for row in rows
        ]

    @staticmethod
    def _existing_event(
        connection: Connection, operation_id: str
    ) -> dict[str, Any] | None:
        return connection.execute(
            select(UsageEventModel.__table__).where(
                UsageEventModel.operation_id == operation_id
            )
        ).mappings().first()

    @staticmethod
    def _assert_replay(
        existing: dict[str, Any], current: dict[str, Any], operation_id: str
    ) -> None:
        if existing != current:
            raise UsageEventConflictError(
                f"Conflicting usage report for operation_id={operation_id}"
            )

    def close(self) -> None:
        if self._quota_service is not None:
            self._quota_service.close()
        if self._owns_engine:
            self._engine.dispose()
