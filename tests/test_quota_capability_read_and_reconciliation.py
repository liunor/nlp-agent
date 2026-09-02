"""Tests for UsageReadService capability querying, billing reconciliation, and meter pricing management."""

from datetime import datetime, timezone
import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from server.quota.management import QuotaManagementService
from server.quota.models import (
    CapabilityUsageEventModel,
    CapabilityUsageItemModel,
    MeterPricingRuleModel,
    QuotaAlertModel,
    QuotaAdjustmentModel,
    QuotaBucketModel,
    QuotaCreditOperationModel,
    QuotaCreditScopeLockModel,
    QuotaDailyRollupModel,
    QuotaGrantModel,
    QuotaLedgerEntryModel,
    QuotaPolicyModel,
    QuotaProviderBillingModel,
    QuotaRoleCreditOperationModel,
    QuotaUsageArchiveBatchModel,
    UsageEventModel,
)
from server.quota.operations import QuotaOperationsService
from server.quota.usage import UsageReadService

UTC = timezone.utc
NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def memory_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for model in (
        CapabilityUsageEventModel,
        CapabilityUsageItemModel,
        MeterPricingRuleModel,
        QuotaAlertModel,
        QuotaAdjustmentModel,
        QuotaBucketModel,
        QuotaCreditOperationModel,
        QuotaCreditScopeLockModel,
        QuotaDailyRollupModel,
        QuotaGrantModel,
        QuotaLedgerEntryModel,
        QuotaPolicyModel,
        QuotaProviderBillingModel,
        QuotaRoleCreditOperationModel,
        QuotaUsageArchiveBatchModel,
        UsageEventModel,
    ):
        model.__table__.create(engine)
    return engine


def test_meter_pricing_rule_management(memory_db):
    mgmt = QuotaManagementService(memory_db)

    # 1. Create rule
    rule = mgmt.create_meter_pricing_rule(
        capability_type="search",
        meter="search.requests",
        pricing_key="qwen/cn-beijing/web-search/turbo",
        version="v1",
        unit="call",
        rate_micro=10_000,
        rate_unit=1,
        min_charge_micro=0,
        effective_from=NOW,
        created_by="admin-1",
    )
    assert rule["pricing_key"] == "qwen/cn-beijing/web-search/turbo"
    assert rule["rate_micro"] == 10_000

    # 2. List rules
    rules = mgmt.list_meter_pricing_rules(capability_type="search")
    assert len(rules) == 1
    assert rules[0]["meter"] == "search.requests"

    # 3. Overlapping version conflict
    from server.quota.errors import QuotaDomainError
    with pytest.raises(QuotaDomainError):
        mgmt.create_meter_pricing_rule(
            capability_type="search",
            meter="search.requests",
            pricing_key="qwen/cn-beijing/web-search/turbo",
            version="v2",
            unit="call",
            rate_micro=15_000,
            rate_unit=1,
            effective_from=NOW,
            created_by="admin-1",
        )


def test_usage_read_service_capability_queries(memory_db):
    usage_reader = UsageReadService(memory_db)
    ev_id = str(uuid.uuid4())

    # Insert a capability event and item
    with memory_db.begin() as conn:
        conn.execute(
            CapabilityUsageEventModel.__table__.insert().values(
                id=ev_id,
                dedupe_key=ev_id,
                idempotency_key=f"idemp:{ev_id}",
                operation_id="op-ocr-1",
                parent_operation_id=None,
                reservation_id="res-1",
                request_id="req-1",
                user_id="user-1",
                workspace_id="ws-1",
                conversation_id="conv-1",
                turn_id="turn-1",
                worker_id=None,
                purpose="vision",
                capability_type="ocr",
                provider="internal",
                pricing_key="internal/rapidocr/v1",
                provider_response_id=None,
                usage_source="measured",
                usage_status="exact",
                credits_micro=500,
                occurred_at=NOW,
                raw_usage_json={"page_count": 1},
                created_at=NOW,
            )
        )
        conn.execute(
            CapabilityUsageItemModel.__table__.insert().values(
                id=str(uuid.uuid4()),
                event_id=ev_id,
                meter="ocr.pages",
                quantity=1,
                unit="page",
                rate_micro=500,
                rate_unit=1,
                line_credits_micro=500,
                created_at=NOW,
            )
        )

    # 1. List events
    events = usage_reader.list_capability_events(user_id="user-1")
    assert len(events) == 1
    assert events[0]["operation_id"] == "op-ocr-1"
    assert events[0]["capability_type"] == "ocr"
    assert len(events[0]["items"]) == 1
    assert events[0]["items"][0]["meter"] == "ocr.pages"

    # 2. Summarize usage
    summary = usage_reader.summarize_capability_usage(user_id="user-1")
    assert summary["total_credits_micro"] == 500
    assert summary["total_events"] == 1
    assert len(summary["by_capability"]) == 1
    assert summary["by_capability"][0]["capability_type"] == "ocr"
    assert summary["by_capability"][0]["meters"]["ocr.pages"] == 1


def test_billing_reconciliation_with_capability_event(memory_db):
    ops = QuotaOperationsService(memory_db)
    ev_id = str(uuid.uuid4())

    # Insert a capability usage event
    with memory_db.begin() as conn:
        conn.execute(
            CapabilityUsageEventModel.__table__.insert().values(
                id=ev_id,
                dedupe_key=ev_id,
                idempotency_key=f"idemp:{ev_id}",
                operation_id="op-cap-search-1",
                parent_operation_id=None,
                reservation_id="res-1",
                request_id="req-1",
                user_id="user-1",
                workspace_id=None,
                conversation_id=None,
                turn_id=None,
                worker_id=None,
                purpose="search",
                capability_type="search",
                provider="qwen",
                pricing_key="qwen/cn-beijing/web-search/turbo",
                provider_response_id=None,
                usage_source="provider",
                usage_status="exact",
                credits_micro=10_000,
                occurred_at=NOW,
                raw_usage_json={"search_count": 1},
                created_at=NOW,
            )
        )

    # Reconcile provider billing matching the capability event
    reconciliation = ops.reconcile_provider_billing(
        [
            {
                "provider": "qwen",
                "statement_id": "stmt-12345",
                "operation_id": "op-cap-search-1",
                "billed_at": NOW,
                "billed_credits_micro": 10_000,
                "idempotency_key": "billing:stmt-12345",
            }
        ],
        now=NOW,
    )

    assert reconciliation["total"] == 1
    assert reconciliation["matched"] == 1
    billing = reconciliation["items"][0]
    assert billing["status"] == "matched"
    assert billing["usage_event_type"] == "capability"
    assert billing["matched_capability_event_id"] == ev_id
    assert billing["local_credits_micro"] == 10_000
    assert billing["difference_micro"] == 0
