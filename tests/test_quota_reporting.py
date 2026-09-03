"""Unit tests for DurableModelUsageReporter and atomic nlp_usage_events persistence."""

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import insert, select

from core.model_runtime.reporters import UsageEventConflictError
from core.model_runtime.usage import (
    CanonicalTokenUsage,
    InvocationOutcome,
    ModelIdentity,
    ModelInvocation,
    UsageAttributionContext,
)
from server.quota.models import PricingRuleModel, UsageEventModel
from server.quota.reporting import DurableModelUsageReporter


UTC = timezone.utc
AT_START = datetime(2026, 8, 29, 8, 0, tzinfo=UTC)
AT_END = datetime(2026, 8, 29, 8, 1, tzinfo=UTC)


def _invocation(*, operation_id: str | None = None, attempt: int = 1, fallback_index: int = 0) -> ModelInvocation:
    return ModelInvocation(
        operation_id=operation_id or str(uuid.uuid4()),
        identity=ModelIdentity(
            provider="deepseek",
            provider_model="deepseek-v4-pro",
            model_profile="teaching-pro",
            preset="coordinator-pro",
            route="coordinator",
            pricing_key="deepseek/deepseek-v4-pro",
            context_window_tokens=1_000_000,
            max_output_tokens=32_000,
        ),
        attribution=UsageAttributionContext(
            request_id="req-test-1",
            user_id="user-test-1",
            workspace_id="workspace-test-1",
            conversation_id="conv-test-1",
            turn_id="turn-test-1",
            reservation_id="res-test-1",
            worker_id="worker-test-1",
            parent_operation_id=None,
            purpose="coordinator",
        ),
        attempt=attempt,
        fallback_index=fallback_index,
        started_at=AT_START,
    )


def _outcome(*, status: str = "succeeded") -> InvocationOutcome:
    return InvocationOutcome(
        status=status,
        finish_reason="stop",
        error_kind=None,
        completed_at=AT_END,
    )


def _insert_pricing_rule(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(PricingRuleModel).values(
                id=str(uuid.uuid4()),
                pricing_key="deepseek/deepseek-v4-pro",
                version="2026-08-29",
                effective_from=datetime(2026, 1, 1, tzinfo=UTC),
                effective_until=None,
                ordinary_input_credits_micro_per_million_tokens=3_000_000,
                cached_input_credits_micro_per_million_tokens=1_000_000,
                cache_write_credits_micro_per_million_tokens=2_000_000,
                output_credits_micro_per_million_tokens=4_000_000,
                reasoning_output_credits_micro_per_million_tokens=8_000_000,
                status="active",
                created_by="system",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )


@pytest.mark.asyncio
async def test_durable_reporter_persists_exact_attempt_and_shadow_credits(quota_engine):
    """Authoritative Source: PROJECT.md Feature 5 & docs/specs/model-usage-quota-interface-handoff.md § 6, 8."""
    _insert_pricing_rule(quota_engine)
    reporter = DurableModelUsageReporter(quota_engine)
    inv = _invocation(attempt=2, fallback_index=1)
    usage = CanonicalTokenUsage(
        input_tokens=1_000_000,
        cached_input_tokens=100_000,
        cache_write_input_tokens=50_000,
        output_tokens=10_000,
        reasoning_output_tokens=2_000,
        total_tokens=1_010_000,
        source="provider",
        provider_response_id="resp-test-1",
    )
    outcome = _outcome()

    await reporter.report(inv, usage, outcome)

    with quota_engine.connect() as connection:
        row = connection.execute(select(UsageEventModel.__table__)).mappings().one()

    assert row["operation_id"] == inv.operation_id
    assert row["user_id"] == "user-test-1"
    assert row["workspace_id"] == "workspace-test-1"
    assert row["reservation_id"] == "res-test-1"
    assert row["purpose"] == "coordinator"
    assert row["attempt"] == 2
    assert row["fallback_index"] == 1
    assert row["usage_status"] == "exact"
    assert row["usage_source"] == "provider"
    assert row["pricing_version"] == "2026-08-29"
    # ordinary_input = 1_000_000 - 100_000 - 50_000 = 850_000
    # ordinary_output = 10_000 - 2_000 = 8_000
    # numerator = 850_000 * 3.0 + 100_000 * 1.0 + 50_000 * 2.0 + 8_000 * 4.0 + 2_000 * 8.0
    #           = 2_550_000 + 100_000 + 100_000 + 32_000 + 16_000 = 2_798_000
    assert row["credits_micro"] == 2_798_000
    assert row["provider"] == "deepseek"
    assert row["provider_model"] == "deepseek-v4-pro"
    assert row["started_at"] is not None
    assert row["occurred_at"] is not None


@pytest.mark.asyncio
async def test_durable_reporter_idempotent_identical_replay(quota_engine):
    """Authoritative Source: PROJECT.md Feature 6 & docs/specs/model-usage-quota-interface-handoff.md § 8."""
    _insert_pricing_rule(quota_engine)
    reporter = DurableModelUsageReporter(quota_engine)
    inv = _invocation()
    usage = CanonicalTokenUsage(
        input_tokens=20,
        output_tokens=5,
        total_tokens=25,
        source="provider",
    )
    outcome = _outcome()

    # First report
    await reporter.report(inv, usage, outcome)
    # Identical replay must succeed idempotently as a safe no-op
    await reporter.report(inv, usage, outcome)

    with quota_engine.connect() as connection:
        rows = connection.execute(select(UsageEventModel.__table__)).mappings().all()
    assert len(rows) == 1
    assert rows[0]["operation_id"] == inv.operation_id


@pytest.mark.asyncio
async def test_durable_reporter_rejects_conflicting_replay_altered_tokens(quota_engine):
    """Authoritative Source: PROJECT.md Feature 6 & docs/specs/model-usage-quota-interface-handoff.md § 8."""
    _insert_pricing_rule(quota_engine)
    reporter = DurableModelUsageReporter(quota_engine)
    inv = _invocation()
    first = CanonicalTokenUsage(
        input_tokens=20,
        output_tokens=5,
        total_tokens=25,
        source="provider",
    )
    second = CanonicalTokenUsage(
        input_tokens=21,
        output_tokens=5,
        total_tokens=26,
        source="provider",
    )
    outcome = _outcome()

    await reporter.report(inv, first, outcome)

    with pytest.raises(UsageEventConflictError, match="Conflicting usage report"):
        await reporter.report(inv, second, outcome)


@pytest.mark.asyncio
async def test_durable_reporter_rejects_conflicting_replay_altered_identity(quota_engine):
    """Authoritative Source: PROJECT.md Feature 6 & docs/specs/model-usage-quota-interface-handoff.md § 8."""
    _insert_pricing_rule(quota_engine)
    reporter = DurableModelUsageReporter(quota_engine)
    op_id = str(uuid.uuid4())
    inv1 = _invocation(operation_id=op_id)
    inv2 = inv1.model_copy(
        update={
            "identity": inv1.identity.model_copy(
                update={"provider_model": "deepseek-v4-flash"}
            )
        }
    )
    usage = CanonicalTokenUsage(input_tokens=20, output_tokens=5, total_tokens=25, source="provider")
    outcome = _outcome()

    await reporter.report(inv1, usage, outcome)

    with pytest.raises(UsageEventConflictError, match="Conflicting usage report"):
        await reporter.report(inv2, usage, outcome)


@pytest.mark.asyncio
async def test_durable_reporter_unpriced_usage_source_none(quota_engine):
    """Authoritative Source: PROJECT.md Feature 9 & docs/specs/model-usage-quota-interface-handoff.md § 6."""
    _insert_pricing_rule(quota_engine)
    reporter = DurableModelUsageReporter(quota_engine)
    inv = _invocation()
    usage = CanonicalTokenUsage(
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        source="none",
    )
    outcome = _outcome(status="failed")

    await reporter.report(inv, usage, outcome)

    with quota_engine.connect() as connection:
        row = connection.execute(select(UsageEventModel.__table__)).mappings().one()

    assert row["operation_id"] == inv.operation_id
    assert row["usage_source"] == "none"
    assert row["usage_status"] == "pending"
    assert row["credits_micro"] is None
    assert row["pricing_version"] is None


@pytest.mark.asyncio
async def test_durable_reporter_missing_pricing_rule_fails_closed(quota_engine):
    """Authoritative Source: PROJECT.md Feature 9 & docs/specs/model-usage-quota-interface-handoff.md § 6."""
    reporter = DurableModelUsageReporter(quota_engine)
    inv = _invocation()  # pricing_key is not in pricing rules table
    usage = CanonicalTokenUsage(
        input_tokens=50,
        output_tokens=10,
        total_tokens=60,
        source="provider",
    )

    await reporter.report(inv, usage, _outcome())

    with quota_engine.connect() as connection:
        row = connection.execute(select(UsageEventModel.__table__)).mappings().one()

    assert row["operation_id"] == inv.operation_id
    assert row["usage_status"] == "pending"
    assert row["credits_micro"] is None


@pytest.mark.asyncio
async def test_durable_reporter_retry_and_fallback_distinct_operation_ids(quota_engine):
    """Authoritative Source: PROJECT.md Feature 5 & docs/specs/model-usage-quota-interface-handoff.md § 5.1."""
    _insert_pricing_rule(quota_engine)
    reporter = DurableModelUsageReporter(quota_engine)

    # Attempt 1: Failed attempt on primary candidate
    inv1 = _invocation(attempt=1, fallback_index=0)
    usage1 = CanonicalTokenUsage(input_tokens=10, output_tokens=0, total_tokens=10, source="provider")
    outcome1 = InvocationOutcome(status="failed", error_kind="upstream_timeout", completed_at=AT_END)
    await reporter.report(inv1, usage1, outcome1)

    # Attempt 2: Successful retry on fallback candidate
    inv2 = _invocation(attempt=2, fallback_index=1)
    usage2 = CanonicalTokenUsage(input_tokens=10, output_tokens=20, total_tokens=30, source="provider")
    outcome2 = _outcome(status="succeeded")
    await reporter.report(inv2, usage2, outcome2)

    with quota_engine.connect() as connection:
        rows = connection.execute(
            select(UsageEventModel.__table__).order_by(UsageEventModel.attempt)
        ).mappings().all()

    assert len(rows) == 2
    assert rows[0]["operation_id"] == inv1.operation_id
    assert rows[0]["attempt"] == 1
    assert rows[0]["outcome_status"] == "failed"

    assert rows[1]["operation_id"] == inv2.operation_id
    assert rows[1]["attempt"] == 2
    assert rows[1]["fallback_index"] == 1
    assert rows[1]["outcome_status"] == "succeeded"


@pytest.mark.asyncio
async def test_durable_reporter_interrupted_outcome_saved_as_pending(quota_engine):
    """Authoritative Source: PROJECT.md Feature 8 & docs/specs/model-usage-quota-interface-handoff.md § 7."""
    _insert_pricing_rule(quota_engine)
    reporter = DurableModelUsageReporter(quota_engine)
    inv = _invocation()
    usage = CanonicalTokenUsage(input_tokens=100, output_tokens=25, total_tokens=125, source="provider")
    outcome = InvocationOutcome(
        status="interrupted",
        finish_reason=None,
        error_kind="stream_interrupted",
        completed_at=AT_END,
    )

    await reporter.report(inv, usage, outcome)

    with quota_engine.connect() as connection:
        row = connection.execute(select(UsageEventModel.__table__)).mappings().one()

    assert row["operation_id"] == inv.operation_id
    assert row["outcome_status"] == "interrupted"
    assert row["usage_status"] == "pending"


@pytest.mark.asyncio
async def test_durable_reporter_database_failure_propagates(quota_engine):
    """Authoritative Source: PROJECT.md Feature 7 & docs/specs/model-usage-quota-interface-handoff.md § 8."""
    # Dispose engine to force connectivity failure
    quota_engine.dispose()
    reporter = DurableModelUsageReporter(quota_engine)
    inv = _invocation()
    usage = CanonicalTokenUsage(input_tokens=10, output_tokens=10, total_tokens=20, source="provider")

    with pytest.raises(Exception):
        await reporter.report(inv, usage, _outcome())


@pytest.mark.asyncio
async def test_durable_reporter_reconciles_pending_to_exact(quota_engine):
    """Authoritative Source: PROJECT.md Feature 6, 9 & docs/specs/model-usage-quota-interface-handoff.md § 8."""
    reporter = DurableModelUsageReporter(quota_engine)
    inv = _invocation()
    usage = CanonicalTokenUsage(input_tokens=20, output_tokens=5, total_tokens=25, source="provider")

    # Initially reported without pricing rule -> saved as pending
    await reporter.report(inv, usage, _outcome())
    with quota_engine.connect() as connection:
        row1 = connection.execute(select(UsageEventModel.__table__)).mappings().one()
    assert row1["usage_status"] == "pending"
    assert row1["credits_micro"] is None

    # Insert pricing rule now
    _insert_pricing_rule(quota_engine)

    # Reconcile report for the same operation_id
    await reporter.report(inv, usage, _outcome())

    with quota_engine.connect() as connection:
        row2 = connection.execute(select(UsageEventModel.__table__)).mappings().one()
    assert row2["usage_status"] == "exact"
    assert row2["credits_micro"] is not None
    assert row2["credits_micro"] > 0
