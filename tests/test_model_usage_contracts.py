"""Unit tests for model usage contracts, validators, attribution contexts, and InMemory reporter."""

from datetime import datetime, timezone
import uuid

import pytest
from pydantic import ValidationError

from core.model_runtime.reporters import (
    InMemoryModelUsageReporter,
    ModelUsageReporterSlot,
    UsageEventConflictError,
)
from core.model_runtime.usage import (
    CanonicalTokenUsage,
    InvocationOutcome,
    MissingUsageAttributionError,
    ModelIdentity,
    ModelInvocation,
    UsageAttributionContext,
    bind_usage_attribution,
    bind_usage_purpose,
    current_usage_attribution,
    resolve_usage_attribution,
    system_usage_attribution,
)
from core.observability.context import TelemetryContext, bind_telemetry_context


def _sample_identity() -> ModelIdentity:
    return ModelIdentity(
        provider="deepseek",
        provider_model="deepseek-v4-pro",
        model_profile="deepseek",
        preset="coordinator-pro",
        route="coordinator",
        pricing_key="deepseek/deepseek-v4-pro",
        context_window_tokens=1_000_000,
        max_output_tokens=32_000,
    )


def _sample_attribution(purpose: str = "coordinator") -> UsageAttributionContext:
    return UsageAttributionContext(
        request_id="req-123",
        user_id="user-456",
        workspace_id="ws-789",
        conversation_id="conv-1",
        turn_id="turn-1",
        purpose=purpose,
    )


def test_canonical_token_usage_valid():
    usage = CanonicalTokenUsage(
        input_tokens=100,
        cached_input_tokens=30,
        cache_write_input_tokens=20,
        output_tokens=50,
        reasoning_output_tokens=15,
        total_tokens=150,
        source="provider",
        provider_response_id="resp-1",
    )
    assert usage.input_tokens == 100
    assert usage.cached_input_tokens == 30
    assert usage.cache_write_input_tokens == 20
    assert usage.output_tokens == 50
    assert usage.reasoning_output_tokens == 15
    assert usage.total_tokens == 150
    assert usage.source == "provider"
    assert usage.provider_response_id == "resp-1"


def test_canonical_token_usage_default_none_all_zero():
    usage = CanonicalTokenUsage()
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0
    assert usage.total_tokens == 0
    assert usage.source == "none"


def test_canonical_token_usage_provider_all_zero_allowed():
    usage = CanonicalTokenUsage(
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        source="provider",
    )
    assert usage.source == "provider"
    assert usage.total_tokens == 0


def test_canonical_token_usage_rejects_source_none_with_tokens():
    with pytest.raises(ValidationError, match="source=none cannot carry token values"):
        CanonicalTokenUsage(
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            source="none",
        )


def test_canonical_token_usage_rejects_total_mismatch():
    with pytest.raises(ValidationError, match="total_tokens must equal input_tokens \\+ output_tokens"):
        CanonicalTokenUsage(
            input_tokens=10,
            output_tokens=10,
            total_tokens=25,
            source="provider",
        )


def test_canonical_token_usage_rejects_cached_exceeding_input():
    with pytest.raises(
        ValidationError,
        match="cached_input_tokens \\+ cache_write_input_tokens must not exceed input_tokens",
    ):
        CanonicalTokenUsage(
            input_tokens=50,
            cached_input_tokens=40,
            cache_write_input_tokens=20,
            output_tokens=10,
            total_tokens=60,
            source="provider",
        )


def test_canonical_token_usage_rejects_reasoning_exceeding_output():
    with pytest.raises(ValidationError, match="reasoning_output_tokens must be a subset of output_tokens"):
        CanonicalTokenUsage(
            input_tokens=50,
            output_tokens=20,
            reasoning_output_tokens=25,
            total_tokens=70,
            source="provider",
        )


def test_canonical_token_usage_strict_int_types():
    with pytest.raises(ValidationError):
        CanonicalTokenUsage(
            input_tokens=-1,
            output_tokens=0,
            total_tokens=-1,
            source="provider",
        )
    with pytest.raises(ValidationError):
        CanonicalTokenUsage(
            input_tokens=True,  # bool should be rejected
            output_tokens=0,
            total_tokens=1,
            source="provider",
        )
    with pytest.raises(ValidationError):
        CanonicalTokenUsage(
            input_tokens=12.5,  # float should be rejected
            output_tokens=0,
            total_tokens=12,
            source="provider",
        )


def test_model_invocation_valid():
    op_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    inv = ModelInvocation(
        operation_id=op_id,
        identity=_sample_identity(),
        attribution=_sample_attribution(),
        attempt=1,
        fallback_index=0,
        started_at=now,
    )
    assert inv.operation_id == op_id
    assert inv.started_at == now


def test_model_invocation_rejects_invalid_operation_id():
    with pytest.raises(ValidationError, match="operation_id must be a UUIDv4"):
        ModelInvocation(
            operation_id="invalid-uuid",
            identity=_sample_identity(),
            attribution=_sample_attribution(),
            attempt=1,
            fallback_index=0,
            started_at=datetime.now(timezone.utc),
        )


def test_model_invocation_rejects_naive_or_non_utc_time():
    op_id = str(uuid.uuid4())
    with pytest.raises(ValidationError, match="started_at must be timezone-aware"):
        ModelInvocation(
            operation_id=op_id,
            identity=_sample_identity(),
            attribution=_sample_attribution(),
            attempt=1,
            fallback_index=0,
            started_at=datetime.now(),  # naive
        )


def test_invocation_outcome_valid_and_validation():
    now = datetime.now(timezone.utc)
    outcome = InvocationOutcome(
        status="succeeded",
        finish_reason="stop",
        error_kind=None,
        completed_at=now,
    )
    assert outcome.status == "succeeded"
    assert outcome.completed_at == now

    with pytest.raises(ValidationError, match="completed_at must be timezone-aware"):
        InvocationOutcome(
            status="failed",
            completed_at=datetime.now(),  # naive
        )


def test_attribution_context_nesting_and_restoration():
    assert current_usage_attribution() is None
    ctx1 = _sample_attribution("coordinator")
    ctx2 = _sample_attribution("worker")

    with bind_usage_attribution(ctx1):
        assert current_usage_attribution() == ctx1
        with bind_usage_attribution(ctx2):
            assert current_usage_attribution() == ctx2
        assert current_usage_attribution() == ctx1
    assert current_usage_attribution() is None


def test_bind_usage_purpose_overrides_and_restores():
    ctx = _sample_attribution("coordinator")
    with bind_usage_attribution(ctx):
        assert resolve_usage_attribution().purpose == "coordinator"
        with bind_usage_purpose("compact"):
            assert resolve_usage_attribution().purpose == "compact"
            with bind_usage_purpose("memory"):
                assert resolve_usage_attribution().purpose == "memory"
            assert resolve_usage_attribution().purpose == "compact"
        assert resolve_usage_attribution().purpose == "coordinator"


def test_resolve_usage_attribution_from_telemetry():
    assert current_usage_attribution() is None
    telem = TelemetryContext.create(
        session_id="session-1",
        turn_id="turn-1",
        workspace_id="ws-1",
        user_id="user-1",
    )
    with bind_telemetry_context(telem):
        resolved = resolve_usage_attribution()
        assert resolved.user_id == "user-1"
        assert resolved.workspace_id == "ws-1"
        assert resolved.conversation_id == "session-1"
        assert resolved.turn_id == "turn-1"
        assert resolved.purpose == "coordinator"

        with bind_usage_purpose("vision"):
            resolved_purpose = resolve_usage_attribution()
            assert resolved_purpose.purpose == "vision"


def test_resolve_usage_attribution_missing_raises():
    with pytest.raises(MissingUsageAttributionError):
        resolve_usage_attribution()


def test_system_usage_attribution():
    with system_usage_attribution(purpose="evaluation") as ctx:
        assert ctx.user_id == "system"
        assert ctx.workspace_id is None
        assert ctx.purpose == "evaluation"
        assert len(ctx.request_id) > 0
        assert resolve_usage_attribution() == ctx
    assert current_usage_attribution() is None


@pytest.mark.asyncio
async def test_in_memory_reporter_idempotency_and_conflicts():
    reporter = InMemoryModelUsageReporter()
    op_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    inv = ModelInvocation(
        operation_id=op_id,
        identity=_sample_identity(),
        attribution=_sample_attribution(),
        attempt=1,
        fallback_index=0,
        started_at=now,
    )
    usage = CanonicalTokenUsage(
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        source="provider",
    )
    outcome = InvocationOutcome(
        status="succeeded",
        finish_reason="stop",
        completed_at=now,
    )

    # 1. Report once
    await reporter.report(inv, usage, outcome)
    assert len(reporter.events) == 1
    assert reporter.events[0] == (inv, usage, outcome)

    # 2. Duplicate exact report should succeed idempotently without adding another record
    await reporter.report(inv, usage, outcome)
    assert len(reporter.events) == 1

    # 3. Report same op_id with different usage/outcome should raise Conflict
    conflicting_usage = CanonicalTokenUsage(
        input_tokens=20,
        output_tokens=5,
        total_tokens=25,
        source="provider",
    )
    with pytest.raises(UsageEventConflictError, match="Conflicting usage report"):
        await reporter.report(inv, conflicting_usage, outcome)


def test_reporter_slot_sharing():
    slot = ModelUsageReporterSlot()
    reporter = InMemoryModelUsageReporter()
    assert slot.reporter is None
    slot.configure(reporter)
    assert slot.reporter is reporter
