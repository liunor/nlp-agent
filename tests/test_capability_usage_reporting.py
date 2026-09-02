"""Integration tests for DurableCapabilityUsageReporter and QuotaService settlement."""

from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine, select

from sqlalchemy.pool import StaticPool

from core.usage_metering.contracts import (
    CapabilityUsageEvent,
    MeterPricingRule,
    MeteredUsageItem,
)
from core.usage_metering.reporters import CapabilityUsageConflictError
from server.quota.bootstrap import (
    configure_capability_usage_reporter,
    shutdown_capability_usage_reporter,
)
from server.quota.capability_reporting import DurableCapabilityUsageReporter
from server.quota.errors import UsageReporterConfigurationError
from server.quota.meter_pricing import MeterPricingCatalog
from server.quota.models import (
    CapabilityUsageEventModel,
    CapabilityUsageItemModel,
    MeterPricingRuleModel,
    PolicyBindingModel,
    QuotaBucketModel,
    QuotaLedgerEntryModel,
    QuotaPolicyModel,
    QuotaReservationModel,
)
from server.quota.service import QuotaService

UTC = timezone.utc


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for model in (
        CapabilityUsageEventModel,
        CapabilityUsageItemModel,
        MeterPricingRuleModel,
        QuotaReservationModel,
        QuotaLedgerEntryModel,
        QuotaBucketModel,
        QuotaPolicyModel,
        PolicyBindingModel,
    ):
        model.__table__.create(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def pricing_catalog():
    rules = [
        MeterPricingRule(
            pricing_key="qwen/cn-beijing/web-search/turbo",
            version="2026-09-01",
            meter="search.requests",
            unit="call",
            rate_unit=1,
            rate_micro=3000,
            minimum_charge_micro=0,
            effective_from=datetime(2026, 9, 1, tzinfo=UTC),
        ),
        MeterPricingRule(
            pricing_key="internal/web-fetch/v1",
            version="2026-09-01",
            meter="web_fetch.requests",
            unit="call",
            rate_unit=1,
            rate_micro=500,
            minimum_charge_micro=0,
            effective_from=datetime(2026, 9, 1, tzinfo=UTC),
        ),
        MeterPricingRule(
            pricing_key="internal/web-fetch/v1",
            version="2026-09-01",
            meter="web_fetch.bytes",
            unit="byte",
            rate_unit=1000,
            rate_micro=1,
            minimum_charge_micro=0,
            effective_from=datetime(2026, 9, 1, tzinfo=UTC),
        ),
    ]
    return MeterPricingCatalog(rules)


@pytest.mark.asyncio
async def test_durable_capability_reporting_persists_event_and_items(
    db_engine, pricing_catalog
):
    quota_service = QuotaService(db_engine)
    reporter = DurableCapabilityUsageReporter(
        db_engine,
        quota_service=quota_service,
        pricing_catalog=pricing_catalog,
    )

    event = CapabilityUsageEvent(
        operation_id="search-op-1",
        request_id="req-1",
        user_id="user-1",
        purpose="worker",
        capability_type="search",
        provider="qwen",
        pricing_key="qwen/cn-beijing/web-search/turbo",
        usage_source="provider",
        usage_status="exact",
        items=(MeteredUsageItem(meter="search.requests", quantity=2, unit="call"),),
        occurred_at=datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC),
    )

    await reporter.report(event)

    with db_engine.connect() as conn:
        ev_row = conn.execute(
            select(CapabilityUsageEventModel).where(
                CapabilityUsageEventModel.operation_id == "search-op-1"
            )
        ).mappings().first()
        assert ev_row is not None
        assert ev_row["credits_micro"] == 6000
        assert ev_row["pricing_version"] == "2026-09-01"

        items = conn.execute(
            select(CapabilityUsageItemModel).where(
                CapabilityUsageItemModel.event_id == ev_row["id"]
            )
        ).mappings().all()
        assert len(items) == 1
        assert items[0]["meter"] == "search.requests"
        assert items[0]["quantity"] == 2
        assert items[0]["line_credits_micro"] == 6000

    # Idempotent duplicate report
    await reporter.report(event)
    with db_engine.connect() as conn:
        items = conn.execute(
            select(CapabilityUsageItemModel).where(
                CapabilityUsageItemModel.event_id == ev_row["id"]
            )
        ).mappings().all()
        assert len(items) == 1


@pytest.mark.asyncio
async def test_durable_capability_reporting_conflicting_report_raises(
    db_engine, pricing_catalog
):
    reporter = DurableCapabilityUsageReporter(
        db_engine,
        pricing_catalog=pricing_catalog,
    )

    event = CapabilityUsageEvent(
        operation_id="search-op-conf",
        request_id="req-1",
        user_id="user-1",
        purpose="worker",
        capability_type="search",
        provider="qwen",
        pricing_key="qwen/cn-beijing/web-search/turbo",
        usage_source="provider",
        usage_status="exact",
        items=(MeteredUsageItem(meter="search.requests", quantity=1, unit="call"),),
        occurred_at=datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC),
    )
    await reporter.report(event)

    conflicting_event = CapabilityUsageEvent(
        operation_id="search-op-conf",
        request_id="req-1",
        user_id="user-1",
        purpose="worker",
        capability_type="search",
        provider="qwen",
        pricing_key="qwen/cn-beijing/web-search/turbo",
        usage_source="provider",
        usage_status="exact",
        items=(MeteredUsageItem(meter="search.requests", quantity=5, unit="call"),),  # Different quantity
        occurred_at=datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC),
    )
    with pytest.raises(CapabilityUsageConflictError, match="Conflicting capability usage report"):
        await reporter.report(conflicting_event)


@pytest.mark.asyncio
async def test_durable_capability_reporting_pending_to_exact_reconciliation(
    db_engine, pricing_catalog
):
    quota_service = QuotaService(db_engine)
    reporter = DurableCapabilityUsageReporter(
        db_engine,
        quota_service=quota_service,
        pricing_catalog=pricing_catalog,
    )

    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)

    # 1. Report pending event
    event_pending = CapabilityUsageEvent(
        operation_id="web-op-recon",
        request_id="req-1",
        user_id="user-1",
        purpose="worker",
        capability_type="web_fetch",
        provider="internal",
        pricing_key="internal/web-fetch/v1",
        usage_source="measured",
        usage_status="pending",
        items=(
            MeteredUsageItem(meter="web_fetch.requests", quantity=1, unit="call"),
            MeteredUsageItem(meter="web_fetch.bytes", quantity=0, unit="byte"),
        ),
        occurred_at=now,
    )
    await reporter.report(event_pending)

    with db_engine.connect() as conn:
        ev = conn.execute(
            select(CapabilityUsageEventModel).where(
                CapabilityUsageEventModel.operation_id == "web-op-recon"
            )
        ).mappings().first()
        assert ev["usage_status"] == "pending"
        assert ev["credits_micro"] is None

    # 2. Report exact event with measured bytes
    event_exact = CapabilityUsageEvent(
        operation_id="web-op-recon",
        request_id="req-1",
        user_id="user-1",
        purpose="worker",
        capability_type="web_fetch",
        provider="internal",
        pricing_key="internal/web-fetch/v1",
        usage_source="measured",
        usage_status="exact",
        items=(
            MeteredUsageItem(meter="web_fetch.requests", quantity=1, unit="call"),
            MeteredUsageItem(meter="web_fetch.bytes", quantity=5000, unit="byte"),  # 5000 * 1 / 1000 = 5 microcredits
        ),
        occurred_at=now,
    )
    await reporter.report(event_exact)

    with db_engine.connect() as conn:
        ev = conn.execute(
            select(CapabilityUsageEventModel).where(
                CapabilityUsageEventModel.operation_id == "web-op-recon"
            )
        ).mappings().first()
        assert ev["usage_status"] == "exact"
        # 500 (call) + 5 (bytes) = 505 microcredits
        assert ev["credits_micro"] == 505


def test_capability_usage_reporter_bootstrap_failure():
    with pytest.raises(UsageReporterConfigurationError, match="Durable capability usage Reporter is required"):
        configure_capability_usage_reporter("", required=True)
