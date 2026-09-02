"""Integration tests for Turn admission, multimodal/capability settlement, and finish flow."""

from datetime import datetime, timezone
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.pool import StaticPool

from core.usage_metering.contracts import CapabilityUsageEvent, MeterPricingRule, MeteredUsageItem
from server.quota.capability_reporting import DurableCapabilityUsageReporter
from server.quota.contracts import AdmitTurn, FinishTurn
from server.quota.meter_pricing import MeterPricingCatalog
from server.quota.models import (
    CapabilityUsageEventModel,
    CapabilityUsageItemModel,
    MeterPricingRuleModel,
    PolicyBindingModel,
    QuotaAdjustmentModel,
    QuotaBucketModel,
    QuotaConcurrencyLockModel,
    QuotaGrantModel,
    QuotaLedgerEntryModel,
    QuotaPolicyModel,
    QuotaReservationModel,
)
from server.quota.service import QuotaService

UTC = timezone.utc
NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def quota_db():
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
        QuotaConcurrencyLockModel,
        QuotaPolicyModel,
        PolicyBindingModel,
        QuotaGrantModel,
        QuotaAdjustmentModel,
    ):
        model.__table__.create(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _setup_policy(engine, *, daily: int = 1_000_000, request: int = 100_000):
    policy_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            insert(QuotaPolicyModel).values(
                id=policy_id,
                code="student-default",
                version="2026-08-29.1",
                name="Student default",
                status="active",
                request_limit_micro=request,
                daily_limit_micro=daily,
                weekly_limit_micro=None,
                concurrency_limit=5,
                max_overdraft_micro=10_000,
                allowed_model_profiles=["economy"],
                unlimited=False,
                effective_from=NOW,
                effective_until=None,
                created_by="admin",
            )
        )
        connection.execute(
            insert(PolicyBindingModel).values(
                id=str(uuid4()),
                subject_type="default",
                subject_id="*",
                policy_id=policy_id,
                priority=1,
                status="active",
                effective_from=NOW,
                effective_until=None,
            )
        )


@pytest.mark.asyncio
async def test_turn_lifecycle_with_capability_settlement(quota_db):
    _setup_policy(quota_db)
    service = QuotaService(quota_db, lease_seconds=60)

    # 1. Admit turn (reserves 50,000 microcredits)
    admit_result = service.admit_turn(
        AdmitTurn(
            request_id="req-multi-1",
            user_id="user-multi-1",
            turn_id="turn-multi-1",
            model_profile="economy",
            model_role="coordinator",
            estimated_input_tokens=100,
            estimated_output_tokens=100,
            estimated_micro=50_000,
            idempotency_key="admit:turn-multi-1",
        ),
        now=NOW,
    )
    assert admit_result.allowed is True
    reservation_id = admit_result.reservation_id
    assert reservation_id is not None

    # 2. Setup meter pricing catalog and capability reporter
    pricing_rule = MeterPricingRule(
        pricing_key="qwen/cn-beijing/web-search/turbo",
        version="2026-09-01",
        meter="search.requests",
        unit="call",
        rate_unit=1,
        rate_micro=3000,
        effective_from=datetime(2026, 9, 1, tzinfo=UTC),
    )
    catalog = MeterPricingCatalog([pricing_rule])
    cap_reporter = DurableCapabilityUsageReporter(
        quota_db,
        quota_service=service,
        pricing_catalog=catalog,
    )

    # 3. Capability execution during the turn
    event = CapabilityUsageEvent(
        operation_id="turn-multi-1:search",
        reservation_id=reservation_id,
        turn_id="turn-multi-1",
        request_id="turn-multi-1",
        user_id="user-multi-1",
        purpose="worker",
        capability_type="search",
        provider="qwen",
        pricing_key="qwen/cn-beijing/web-search/turbo",
        usage_source="provider",
        usage_status="exact",
        items=(MeteredUsageItem(meter="search.requests", quantity=2, unit="call"),),  # 2 * 3000 = 6000 microcredits
        occurred_at=NOW,
    )
    await cap_reporter.report(event)

    # Verify reservation has settled 6000 microcredits so far
    with quota_db.connect() as conn:
        res = conn.execute(
            select(QuotaReservationModel).where(QuotaReservationModel.id == reservation_id)
        ).mappings().first()
        assert res["settled_micro"] == 6000

    # 4. Finish turn: reserved 50,000, settled 6000 -> released 44,000
    finish_result = service.finish_turn(
        FinishTurn(
            reservation_id=reservation_id,
            turn_id="turn-multi-1",
            idempotency_key="finish:turn-multi-1",
        ),
        now=NOW,
    )
    assert finish_result.status == "settled"
    assert finish_result.released_micro == 44_000

    with quota_db.connect() as conn:
        res = conn.execute(
            select(QuotaReservationModel).where(QuotaReservationModel.id == reservation_id)
        ).mappings().first()
        assert res["status"] == "settled"
        assert res["reserved_micro"] == 0
        assert res["settled_micro"] == 6000
