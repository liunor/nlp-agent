"""Durable non-model capability usage Reporter and transactional settlement."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, create_engine, delete, insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from core.usage_metering.contracts import (
    CapabilityUsageEvent,
    MeterPricingRule,
    PricedCapabilityUsage,
)
from core.usage_metering.reporters import (
    CapabilityUsageConflictError,
    CapabilityUsageReporter,
)
from server.quota.meter_pricing import (
    MeterPricingCatalog,
    UnknownMeterPricingError,
)
from server.quota.models import (
    CapabilityUsageEventModel,
    CapabilityUsageItemModel,
    MeterPricingRuleModel,
)
from server.quota.service import QuotaService


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _db_time(value: datetime) -> datetime:
    return _utc(value).replace(tzinfo=None)


class DurableCapabilityUsageReporter(CapabilityUsageReporter):
    """Persist each capability invocation idempotently by operation_id."""

    def __init__(
        self,
        database: str | Engine,
        *,
        quota_service: QuotaService | None = None,
        pricing_catalog: MeterPricingCatalog | None = None,
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
        self._fixed_pricing_catalog = pricing_catalog

    def close(self) -> None:
        if self._owns_engine:
            self._engine.dispose()

    async def report(self, event: CapabilityUsageEvent) -> None:
        await asyncio.to_thread(self._report_sync, event)

    def _get_catalog(self, connection: Connection, pricing_key: str) -> MeterPricingCatalog:
        if self._fixed_pricing_catalog is not None:
            return self._fixed_pricing_catalog
        rows = connection.execute(
            select(MeterPricingRuleModel).where(
                MeterPricingRuleModel.pricing_key == pricing_key,
                MeterPricingRuleModel.status == "active",
            )
        ).mappings().all()
        rules = [
            MeterPricingRule(
                pricing_key=row["pricing_key"],
                version=row["version"],
                meter=row["meter"],
                unit=row["unit"],
                rate_unit=row["rate_unit"],
                rate_micro=row["rate_micro"],
                minimum_charge_micro=row["minimum_charge_micro"],
                effective_from=row["effective_from"].replace(tzinfo=timezone.utc) if row["effective_from"].tzinfo is None else row["effective_from"],
                effective_until=row["effective_until"].replace(tzinfo=timezone.utc) if row["effective_until"] and row["effective_until"].tzinfo is None else row["effective_until"],
                status=row["status"],
                created_by=row["created_by"],
                created_at=row["created_at"],
            )
            for row in rows
        ]
        return MeterPricingCatalog(rules)

    def _report_sync(self, event: CapabilityUsageEvent) -> None:
        payload = event.model_dump(mode="json")
        reservation_id_to_notify = event.reservation_id

        try:
            with self._engine.begin() as connection:
                catalog = self._get_catalog(connection, event.pricing_key)
                priced: PricedCapabilityUsage = catalog.price_event(event)

                existing = self._existing_event(connection, event.operation_id)
                if existing is not None:
                    reservation_id_to_notify = (
                        existing["reservation_id"] or reservation_id_to_notify
                    )
                    existing_payload = existing["raw_usage_json"]
                    current_status = existing["usage_status"]
                    next_status = event.usage_status

                    if existing_payload == payload and not (
                        current_status in {"pending", "unavailable"}
                        and next_status in {"exact", "estimated"}
                    ):
                        # Idempotent replay of the exact same event
                        if (
                            self._quota_service is not None
                            and existing["reservation_id"]
                        ):
                            self._settle_in_transaction(
                                connection,
                                reservation_id=existing["reservation_id"],
                                event=event,
                                priced=priced,
                            )
                    elif (
                        current_status in {"pending", "unavailable"}
                        and next_status in {"exact", "estimated"}
                    ):
                        reservation_id = (
                            existing["reservation_id"] or event.reservation_id
                        )
                        if self._quota_service is not None and reservation_id:
                            self._quota_service.reconcile_usage_in_transaction(
                                connection,
                                reservation_id=reservation_id,
                                operation_id=event.operation_id,
                                credits_micro=priced.credits_micro,
                                usage_status=next_status,
                                usage_source=event.usage_source,
                                pricing_key=event.pricing_key,
                                pricing_version=priced.pricing_version,
                                now=_utc(event.occurred_at),
                            )
                        connection.execute(
                            update(CapabilityUsageEventModel)
                            .where(CapabilityUsageEventModel.operation_id == event.operation_id)
                            .values(
                                usage_status=next_status,
                                usage_source=event.usage_source,
                                pricing_version=priced.pricing_version,
                                credits_micro=priced.credits_micro,
                                raw_usage_json=payload,
                            )
                        )
                        # Refresh items
                        connection.execute(
                            delete(CapabilityUsageItemModel).where(
                                CapabilityUsageItemModel.event_id == existing["id"]
                            )
                        )
                        for item in priced.items:
                            connection.execute(
                                insert(CapabilityUsageItemModel).values(
                                    id=str(uuid.uuid4()),
                                    event_id=existing["id"],
                                    meter=item.meter,
                                    quantity=item.quantity,
                                    unit=item.unit,
                                    rate_unit=item.rate_unit,
                                    rate_micro=item.rate_micro,
                                    line_credits_micro=item.line_credits_micro,
                                    created_at=_db_time(datetime.now(timezone.utc)),
                                )
                            )
                    else:
                        raise CapabilityUsageConflictError(
                            f"Conflicting capability usage report for {event.operation_id}"
                        )
                else:
                    event_id = str(uuid.uuid4())
                    event_values = {
                        "id": event_id,
                        "operation_id": event.operation_id,
                        "parent_operation_id": event.parent_operation_id,
                        "reservation_id": event.reservation_id,
                        "request_id": event.request_id,
                        "user_id": event.user_id,
                        "workspace_id": event.workspace_id,
                        "conversation_id": event.conversation_id,
                        "turn_id": event.turn_id,
                        "worker_id": event.worker_id,
                        "purpose": event.purpose,
                        "capability_type": event.capability_type,
                        "provider": event.provider,
                        "provider_response_id": event.provider_response_id,
                        "pricing_key": event.pricing_key,
                        "pricing_version": priced.pricing_version,
                        "usage_source": event.usage_source,
                        "usage_status": event.usage_status,
                        "credits_micro": priced.credits_micro if event.usage_status in {"exact", "estimated"} else None,
                        "raw_usage_json": payload,
                        "dedupe_key": f"{event.user_id}:{event.capability_type}:{event.operation_id}",
                        "idempotency_key": f"cap-usage:{event.operation_id}",
                        "occurred_at": _db_time(event.occurred_at),
                        "created_at": _db_time(datetime.now(timezone.utc)),
                    }
                    connection.execute(insert(CapabilityUsageEventModel).values(**event_values))
                    for item in priced.items:
                        connection.execute(
                            insert(CapabilityUsageItemModel).values(
                                id=str(uuid.uuid4()),
                                event_id=event_id,
                                meter=item.meter,
                                quantity=item.quantity,
                                unit=item.unit,
                                rate_unit=item.rate_unit,
                                rate_micro=item.rate_micro,
                                line_credits_micro=item.line_credits_micro,
                                created_at=_db_time(datetime.now(timezone.utc)),
                            )
                        )
                    if self._quota_service is not None and event.reservation_id:
                        self._settle_in_transaction(
                            connection,
                            reservation_id=event.reservation_id,
                            event=event,
                            priced=priced,
                        )
        except IntegrityError:
            with self._engine.connect() as connection:
                existing = self._existing_event(connection, event.operation_id)
            if existing is None:
                raise
            return self._report_sync(event)

        if self._quota_service is not None and reservation_id_to_notify:
            self._quota_service.notify_reservation(reservation_id_to_notify)

    def _settle_in_transaction(
        self,
        connection: Connection,
        *,
        reservation_id: str,
        event: CapabilityUsageEvent,
        priced: PricedCapabilityUsage,
    ) -> None:
        assert self._quota_service is not None
        self._quota_service.settle_usage_in_transaction(
            connection,
            reservation_id=reservation_id,
            operation_id=event.operation_id,
            credits_micro=priced.credits_micro,
            usage_status=event.usage_status,
            usage_source=event.usage_source,
            pricing_key=event.pricing_key,
            pricing_version=priced.pricing_version,
            now=_utc(event.occurred_at),
        )

    def _existing_event(
        self, connection: Connection, operation_id: str
    ) -> dict[str, Any] | None:
        row = connection.execute(
            select(CapabilityUsageEventModel).where(
                CapabilityUsageEventModel.operation_id == operation_id
            )
        ).mappings().first()
        return dict(row) if row is not None else None
