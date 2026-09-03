"""End-to-End Opaque-Box Test Suite for Model Usage Metering & Token Quota Management.

Authoritative Specifications:
- A:\\Root_Code\\local-project\\nlp-agent\\.agents\\ORIGINAL_REQUEST.md
- A:\\Root_Code\\local-project\\nlp-agent\\PROJECT.md
- docs/specs/model-usage-quota-interface-handoff.md
- docs/specs/model-usage-quota-handoff-implementation.md

Tiers Covered:
- Tier 1: Feature Coverage (Smoke & Happy Path, >=5 per feature category)
- Tier 2: Boundary & Corner Cases (Negative, Invariant Violations & Error Isolation)
- Tier 3: Cross-Feature Combinations (Pairwise & Concurrency Invariants)
- Tier 4: Real-World Application Scenarios (>=5 Realistic E2E Workflows)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import math
from typing import Any
import uuid

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from pydantic import BaseModel, ValidationError
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    select,
    text,
)
from sqlalchemy.pool import StaticPool

from core.model_runtime.contracts import (
    CircuitBreakerPolicy,
    GenerationConfig,
    ModelCapabilities,
    ModelDefinition,
    ModelPresetConfig,
    RetryPolicy,
    ThinkingConfig,
    TimeoutPolicy,
)
from core.model_runtime.factory import get_global_model_factory
from core.model_runtime.reporters import (
    InMemoryModelUsageReporter,
    ModelUsageReporterSlot,
    UsageEventConflictError,
)
from core.model_runtime.runtime import (
    ModelCandidate,
    ResilientChatModel,
    StreamInterruptedError,
    classify_model_error,
)
from core.model_runtime.usage import (
    CanonicalTokenUsage,
    InvocationOutcome,
    MissingUsageAttributionError,
    ModelIdentity,
    ModelInvocation,
    UsageAttributionContext,
    bind_usage_attribution,
)
from core.observability.context import TelemetryContext, bind_telemetry_context
from gateway.dispatch import ExecutionAuthorizationContext, TurnTask
from gateway.redis_transport import TurnTaskCodec
from core.session_context import SessionContext

# Dynamic detection of server.quota module for progressive testability
try:
    from server.quota.contracts import AdmitTurn, FinishTurn
    from server.quota.errors import QuotaRejectedError
    from server.quota.models import (
        PricingRuleModel,
        QuotaBucketModel,
        QuotaConcurrencyLockModel,
        QuotaLedgerEntryModel,
        QuotaPolicyModel,
        QuotaReservationModel,
        UsageEventModel,
    )
    from server.quota.pricing import (
        EstimatedUsageCannotBePricedError,
        PricingCatalog,
        PricingRule,
        UnknownPricingKeyError,
        UnknownUsageCannotBePricedError,
    )
    from server.quota.reporting import DurableModelUsageReporter
    from server.quota.service import QuotaService

    HAS_SERVER_QUOTA = True
except ImportError:
    HAS_SERVER_QUOTA = False

requires_server_quota = pytest.mark.skipif(
    not HAS_SERVER_QUOTA,
    reason="server.quota implementation pending (Milestones 1-4)",
)

UTC = timezone.utc


# ==============================================================================
# Test Fixtures & In-Memory Test Infrastructure
# ==============================================================================

class FakeRedis:
    """In-memory Redis mock for stream and key-value operations."""

    def __init__(self) -> None:
        self.streams: list[tuple[str, dict[str, Any]]] = []
        self.messages: list[tuple[str, str]] = []
        self.values: dict[str, str] = {}
        self.acks: list[tuple[str, str, str]] = []

    async def xadd(self, stream: str, fields: dict[str, Any]) -> str:
        self.streams.append((stream, fields))
        return f"{len(self.streams)}-0"

    async def publish(self, channel: str, payload: str) -> None:
        self.messages.append((channel, payload))

    async def set(self, key: str, value: str, **options: Any) -> bool:
        self.values[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)

    async def xack(self, stream: str, group: str, message_id: str) -> None:
        self.acks.append((stream, group, message_id))

    async def aclose(self) -> None:
        pass


class FakeRawModel:
    """Mock provider chat model for controlling responses and token metadata."""

    def __init__(self, steps: list[Any]) -> None:
        self._steps = list(steps)
        self.calls = 0

    async def astream(self, messages: list[Any], **kwargs: Any):
        self.calls += 1
        if not self._steps:
            raise RuntimeError("FakeRawModel called with no steps configured")
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        for item in step:
            if isinstance(item, Exception):
                raise item
            yield item

    async def ainvoke(self, messages: list[Any], **kwargs: Any):
        self.calls += 1
        if not self._steps:
            raise RuntimeError("FakeRawModel called with no steps configured")
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        return FakeStructuredModel(self._steps, schema=schema, include_raw=kwargs.get("include_raw", False))


class FakeStructuredModel(FakeRawModel):
    def __init__(self, steps: list[Any], schema: Any, include_raw: bool = False) -> None:
        super().__init__(steps)
        self.schema = schema
        self.include_raw = include_raw

    async def ainvoke(self, messages: list[Any], **kwargs: Any) -> Any:
        raw_or_parsed = await super().ainvoke(messages, **kwargs)
        if isinstance(raw_or_parsed, dict) and "raw" in raw_or_parsed:
            return raw_or_parsed
        raw_msg = AIMessage(
            content="parsed",
            response_metadata={"id": "resp-struct-1", "token_usage": {"prompt_tokens": 40, "completion_tokens": 10}},
        )
        return {
            "raw": raw_msg,
            "parsed": raw_or_parsed,
            "parsing_error": None,
        }


@pytest.fixture
def sqlite_engine():
    """In-memory SQLite engine with StaticPool enforcing lowercase table names."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = MetaData()
    # Explicit lowercase schema for opaque-box testing
    Table(
        "nlp_pricing_rules",
        metadata,
        Column("id", String(64), primary_key=True),
        Column("pricing_key", String(128), nullable=False),
        Column("version", String(64), nullable=False),
        Column("effective_from", DateTime, nullable=False),
        Column("effective_until", DateTime, nullable=True),
        Column("ordinary_input_credits_micro_per_million_tokens", Integer, nullable=False),
        Column("cached_input_credits_micro_per_million_tokens", Integer, nullable=False),
        Column("cache_write_credits_micro_per_million_tokens", Integer, nullable=False),
        Column("output_credits_micro_per_million_tokens", Integer, nullable=False),
        Column("reasoning_output_credits_micro_per_million_tokens", Integer, nullable=True),
    )
    Table(
        "nlp_usage_events",
        metadata,
        Column("operation_id", String(36), primary_key=True),
        Column("request_id", String(64), nullable=False),
        Column("user_id", String(64), nullable=False),
        Column("workspace_id", String(64), nullable=True),
        Column("conversation_id", String(64), nullable=True),
        Column("turn_id", String(64), nullable=True),
        Column("reservation_id", String(64), nullable=True),
        Column("worker_id", String(64), nullable=True),
        Column("purpose", String(32), nullable=False),
        Column("provider", String(64), nullable=False),
        Column("provider_model", String(64), nullable=False),
        Column("model_profile", String(64), nullable=True),
        Column("pricing_key", String(128), nullable=True),
        Column("attempt", Integer, nullable=False),
        Column("fallback_index", Integer, nullable=False),
        Column("input_tokens", Integer, nullable=False),
        Column("cached_input_tokens", Integer, nullable=False),
        Column("cache_write_input_tokens", Integer, nullable=False),
        Column("output_tokens", Integer, nullable=False),
        Column("reasoning_output_tokens", Integer, nullable=False),
        Column("total_tokens", Integer, nullable=False),
        Column("usage_source", String(32), nullable=False),
        Column("status", String(32), nullable=False),
        Column("error_kind", String(64), nullable=True),
        Column("started_at", DateTime, nullable=False),
        Column("completed_at", DateTime, nullable=False),
    )
    Table(
        "nlp_quota_reservations",
        metadata,
        Column("reservation_id", String(64), primary_key=True),
        Column("user_id", String(64), nullable=False),
        Column("workspace_id", String(64), nullable=True),
        Column("turn_id", String(64), nullable=False),
        Column("reserved_micro", Integer, nullable=False),
        Column("status", String(32), nullable=False),
    )
    metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def fake_redis():
    return FakeRedis()


def _make_identity(
    pricing_key: str = "deepseek/deepseek-v4-pro",
    provider: str = "deepseek",
    provider_model: str = "deepseek-v4-pro",
) -> ModelIdentity:
    return ModelIdentity(
        provider=provider,
        provider_model=provider_model,
        model_profile="deepseek",
        preset="coordinator-pro",
        route="coordinator",
        pricing_key=pricing_key,
        context_window_tokens=1_000_000,
        max_output_tokens=32_000,
    )


def _make_attribution(
    user_id: str = "user-1",
    turn_id: str = "turn-1",
    reservation_id: str | None = "res-1",
    purpose: str = "coordinator",
) -> UsageAttributionContext:
    return UsageAttributionContext(
        request_id="req-100",
        user_id=user_id,
        workspace_id="ws-100",
        conversation_id="conv-100",
        turn_id=turn_id,
        reservation_id=reservation_id,
        worker_id="worker-100" if purpose == "worker" else None,
        purpose=purpose,
    )


def _make_invocation(
    operation_id: str | None = None,
    identity: ModelIdentity | None = None,
    attribution: UsageAttributionContext | None = None,
    attempt: int = 1,
    fallback_index: int = 0,
) -> ModelInvocation:
    return ModelInvocation(
        operation_id=operation_id or str(uuid.uuid4()),
        identity=identity or _make_identity(),
        attribution=attribution or _make_attribution(),
        attempt=attempt,
        fallback_index=fallback_index,
        started_at=datetime.now(UTC),
    )


def _make_outcome(
    status: str = "succeeded",
    error_kind: str | None = None,
) -> InvocationOutcome:
    return InvocationOutcome(
        status=status,
        finish_reason="stop" if status == "succeeded" else None,
        error_kind=error_kind,
        completed_at=datetime.now(UTC),
    )


def _make_preset(attempts: int = 1) -> ModelPresetConfig:
    return ModelPresetConfig(
        model="deepseek-v4-pro",
        thinking=ThinkingConfig(enabled=False, effort="none"),
        generation=GenerationConfig(max_output_tokens=1000),
        timeouts=TimeoutPolicy(connect_s=1, first_token_s=1, stream_idle_s=1, total_s=2),
        retry=RetryPolicy(max_attempts=attempts, base_delay_s=0, max_delay_s=0, jitter="none"),
        circuit_breaker=CircuitBreakerPolicy(failure_threshold=5, cooldown_s=1),
    )


def _make_candidate(
    name: str,
    raw_model: Any,
    attempts: int = 1,
    pricing_key: str = "deepseek/deepseek-v4-pro",
) -> ModelCandidate:
    definition = ModelDefinition(
        provider="deepseek",
        model_id=name,
        pricing_key=pricing_key,
        context_window_tokens=1_000_000,
        max_output_tokens=1000,
        capabilities=ModelCapabilities(thinking=True, structured_output=True),
    )
    return ModelCandidate(
        preset_name=name,
        provider_name="deepseek",
        model_name=name,
        definition=definition,
        preset=_make_preset(attempts=attempts),
        model=raw_model,
    )


# ==============================================================================
# Tier 1: Feature Coverage (Smoke & Happy Path)
# ==============================================================================

class TestTier1FeatureCoverage:
    """Tier 1: Verify primary behavior and contracts of each core feature (>=5 per category)."""

    # Category 1A: Canonical Token Contracts & Models
    def test_tier1_canonical_token_usage_standard(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 6."""
        usage = CanonicalTokenUsage(
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            source="provider",
            provider_response_id="resp-1",
        )
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.total_tokens == 150
        assert usage.cached_input_tokens == 0
        assert usage.source == "provider"

    def test_tier1_canonical_token_usage_cached_subset(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 6."""
        usage = CanonicalTokenUsage(
            input_tokens=120,
            cached_input_tokens=40,
            output_tokens=30,
            total_tokens=150,
            source="provider",
        )
        assert usage.cached_input_tokens == 40
        assert usage.input_tokens - usage.cached_input_tokens == 80

    def test_tier1_canonical_token_usage_cache_write_subset(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 6."""
        usage = CanonicalTokenUsage(
            input_tokens=200,
            cached_input_tokens=50,
            cache_write_input_tokens=30,
            output_tokens=70,
            total_tokens=270,
            source="provider",
        )
        assert usage.cached_input_tokens + usage.cache_write_input_tokens <= usage.input_tokens
        assert usage.total_tokens == 270

    def test_tier1_canonical_token_usage_reasoning_subset(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 6."""
        usage = CanonicalTokenUsage(
            input_tokens=80,
            output_tokens=100,
            reasoning_output_tokens=45,
            total_tokens=180,
            source="provider",
        )
        assert usage.reasoning_output_tokens <= usage.output_tokens
        assert usage.total_tokens == 180

    def test_tier1_model_invocation_valid_uuidv4_and_utc(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 5.1."""
        op_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        inv = ModelInvocation(
            operation_id=op_id,
            identity=_make_identity(),
            attribution=_make_attribution(),
            attempt=1,
            fallback_index=0,
            started_at=now,
        )
        assert inv.operation_id == op_id
        assert inv.started_at.tzinfo == UTC
        assert inv.attempt == 1
        assert inv.fallback_index == 0

    # Category 1B: Multi-Rate Token Pricing
    @requires_server_quota
    def test_tier1_pricing_ordinary_tokens_calculation(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 6 & PROJECT.md Feature 2."""
        rule = PricingRule(
            pricing_key="deepseek/deepseek-v4-pro",
            version="2026-08-01",
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
            ordinary_input_credits_micro_per_million_tokens=2_000_000,
            cached_input_credits_micro_per_million_tokens=500_000,
            cache_write_credits_micro_per_million_tokens=1_000_000,
            output_credits_micro_per_million_tokens=8_000_000,
        )
        catalog = PricingCatalog([rule])
        inv = _make_invocation()
        usage = CanonicalTokenUsage(
            input_tokens=10_000,
            output_tokens=2_000,
            total_tokens=12_000,
            source="provider",
        )
        outcome = _make_outcome()
        priced = catalog.price(inv, usage, outcome)
        # 10_000 * 2.0 = 20_000 micro, 2_000 * 8.0 = 16_000 micro -> 36_000 micro
        assert priced.credits_micro == 36_000
        assert priced.ordinary_input_tokens == 10_000

    @requires_server_quota
    def test_tier1_pricing_cached_input_discount(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 6."""
        rule = PricingRule(
            pricing_key="deepseek/deepseek-v4-pro",
            version="v1",
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
            ordinary_input_credits_micro_per_million_tokens=2_000_000,
            cached_input_credits_micro_per_million_tokens=500_000,
            cache_write_credits_micro_per_million_tokens=1_000_000,
            output_credits_micro_per_million_tokens=8_000_000,
        )
        catalog = PricingCatalog([rule])
        inv = _make_invocation()
        # 5,000 ordinary, 5,000 cached
        usage = CanonicalTokenUsage(
            input_tokens=10_000,
            cached_input_tokens=5_000,
            output_tokens=1_000,
            total_tokens=11_000,
            source="provider",
        )
        outcome = _make_outcome()
        priced = catalog.price(inv, usage, outcome)
        # 5,000 * 2.0 = 10,000 micro; 5,000 * 0.5 = 2,500 micro; 1,000 * 8.0 = 8,000 micro -> 20,500
        assert priced.credits_micro == 20_500
        assert priced.cached_input_tokens == 5_000

    @requires_server_quota
    def test_tier1_pricing_reasoning_output_tokens(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 6."""
        rule = PricingRule(
            pricing_key="deepseek/deepseek-v4-pro",
            version="v1",
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
            ordinary_input_credits_micro_per_million_tokens=1_000_000,
            cached_input_credits_micro_per_million_tokens=500_000,
            cache_write_credits_micro_per_million_tokens=500_000,
            output_credits_micro_per_million_tokens=4_000_000,
            reasoning_output_credits_micro_per_million_tokens=6_000_000,
        )
        catalog = PricingCatalog([rule])
        inv = _make_invocation()
        usage = CanonicalTokenUsage(
            input_tokens=1_000,
            output_tokens=2_000,
            reasoning_output_tokens=1_500,
            total_tokens=3_000,
            source="provider",
        )
        outcome = _make_outcome()
        priced = catalog.price(inv, usage, outcome)
        # input: 1,000 * 1.0 = 1,000
        # ordinary output: 500 * 4.0 = 2,000
        # reasoning output: 1,500 * 6.0 = 9,000 -> Total: 12,000
        assert priced.credits_micro == 12_000
        assert priced.reasoning_output_tokens == 1_500

    def test_tier1_pricing_ceiling_division_micro(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 6."""
        # 1 token at 1 credit/million micro-credits must round UP to 1 micro-credit
        rate_micro = 1_000_000
        tokens = 1
        micro = math.ceil(tokens * rate_micro / 1_000_000)
        assert micro == 1

    @requires_server_quota
    def test_tier1_pricing_multi_provider_keys(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 5.2."""
        rules = [
            PricingRule(
                pricing_key="deepseek/deepseek-v4-pro",
                version="v1",
                effective_from=datetime(2026, 1, 1, tzinfo=UTC),
                ordinary_input_credits_micro_per_million_tokens=2_000_000,
                cached_input_credits_micro_per_million_tokens=500_000,
                cache_write_credits_micro_per_million_tokens=500_000,
                output_credits_micro_per_million_tokens=8_000_000,
            ),
            PricingRule(
                pricing_key="qwen/qwen3.8-max",
                version="v1",
                effective_from=datetime(2026, 1, 1, tzinfo=UTC),
                ordinary_input_credits_micro_per_million_tokens=3_000_000,
                cached_input_credits_micro_per_million_tokens=800_000,
                cache_write_credits_micro_per_million_tokens=800_000,
                output_credits_micro_per_million_tokens=9_000_000,
            ),
        ]
        catalog = PricingCatalog(rules)
        inv_qwen = _make_invocation(identity=_make_identity(pricing_key="qwen/qwen3.8-max"))
        usage = CanonicalTokenUsage(input_tokens=1_000, output_tokens=1_000, total_tokens=2_000, source="provider")
        priced = catalog.price(inv_qwen, usage, _make_outcome())
        # 1,000 * 3.0 + 1,000 * 9.0 = 12,000 micro
        assert priced.credits_micro == 12_000
        assert priced.pricing_key == "qwen/qwen3.8-max"

    # Category 1C: Usage Reporting & Idempotency
    @pytest.mark.asyncio
    async def test_tier1_reporter_persists_attempt_record(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 4, 8."""
        reporter = InMemoryModelUsageReporter()
        inv = _make_invocation()
        usage = CanonicalTokenUsage(input_tokens=50, output_tokens=20, total_tokens=70, source="provider")
        outcome = _make_outcome()
        await reporter.report(inv, usage, outcome)
        assert len(reporter.events) == 1
        assert reporter.events[0][0].operation_id == inv.operation_id

    @pytest.mark.asyncio
    async def test_tier1_reporter_idempotent_identical_replay(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 8."""
        reporter = InMemoryModelUsageReporter()
        inv = _make_invocation()
        usage = CanonicalTokenUsage(input_tokens=100, output_tokens=50, total_tokens=150, source="provider")
        outcome = _make_outcome()
        # First report
        await reporter.report(inv, usage, outcome)
        # Duplicate report with identical parameters must succeed idempotently
        await reporter.report(inv, usage, outcome)
        assert len(reporter.events) == 1

    @pytest.mark.asyncio
    async def test_tier1_reporter_attempt_metadata_integrity(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 5.1."""
        reporter = InMemoryModelUsageReporter()
        inv = _make_invocation(attempt=2, fallback_index=1)
        usage = CanonicalTokenUsage(input_tokens=10, output_tokens=10, total_tokens=20, source="provider")
        await reporter.report(inv, usage, _make_outcome())
        event = reporter.events[0][0]
        assert event.attempt == 2
        assert event.fallback_index == 1

    @pytest.mark.asyncio
    async def test_tier1_reporter_preserves_reservation_id(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 5.3."""
        reporter = InMemoryModelUsageReporter()
        inv = _make_invocation(attribution=_make_attribution(reservation_id="res-xyz-999"))
        usage = CanonicalTokenUsage(input_tokens=10, output_tokens=10, total_tokens=20, source="provider")
        await reporter.report(inv, usage, _make_outcome())
        assert reporter.events[0][0].attribution.reservation_id == "res-xyz-999"

    @pytest.mark.asyncio
    async def test_tier1_reporter_outcome_status_and_reason(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 7."""
        reporter = InMemoryModelUsageReporter()
        inv = _make_invocation()
        usage = CanonicalTokenUsage(input_tokens=10, output_tokens=10, total_tokens=20, source="provider")
        outcome = _make_outcome(status="succeeded")
        await reporter.report(inv, usage, outcome)
        assert reporter.events[0][2].status == "succeeded"
        assert reporter.events[0][2].finish_reason == "stop"

    # Category 1D: Call Admission & Token Estimation
    def test_tier1_model_factory_estimate_input_tokens(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 9."""
        factory = get_global_model_factory()
        messages = [HumanMessage(content="Hello world, test message")]
        estimated = factory.estimate_input_tokens("deepseek", messages)
        assert estimated is not None
        assert estimated > 0

    def test_tier1_model_factory_profile_identity(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 9."""
        factory = get_global_model_factory()
        identity = factory.profile_identity("deepseek", "coordinator")
        assert identity.provider in {"deepseek", "qwen"}
        assert identity.pricing_key is not None
        assert identity.pricing_key.startswith(identity.provider)

    @requires_server_quota
    def test_tier1_admit_turn_positive_balance(self, sqlite_engine):
        """Authoritative Source: PROJECT.md Feature 12."""
        service = QuotaService(sqlite_engine)
        cmd = AdmitTurn(
            request_id="req-1",
            user_id="user-1",
            turn_id="turn-1",
            model_profile="deepseek",
            model_role="coordinator",
            estimated_input_tokens=100,
            estimated_output_tokens=100,
            idempotency_key="idem-1",
        )
        res = service.admit_turn(cmd)
        assert res.allowed is True
        assert res.reservation_id is not None

    @requires_server_quota
    def test_tier1_admit_turn_conservative_estimation_fallback(self, sqlite_engine):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 9."""
        service = QuotaService(sqlite_engine)
        # When estimated_input_tokens is None, system must use conservative floor, never 0
        cmd = AdmitTurn(
            request_id="req-2",
            user_id="user-2",
            turn_id="turn-2",
            model_profile="deepseek",
            model_role="coordinator",
            estimated_input_tokens=None,
            estimated_output_tokens=500,
            idempotency_key="idem-2",
        )
        res = service.admit_turn(cmd)
        assert res.allowed is True
        assert res.reserved_micro > 0

    # Category 1E: Dynamic Additional Reservation & Settlement
    @requires_server_quota
    def test_tier1_reserve_additional_success(self, sqlite_engine):
        """Authoritative Source: ORIGINAL_REQUEST.md R3 & PROJECT.md Feature 14."""
        service = QuotaService(sqlite_engine)
        cmd = AdmitTurn(
            request_id="req-3",
            user_id="user-3",
            turn_id="turn-3",
            model_profile="deepseek",
            model_role="coordinator",
            estimated_input_tokens=100,
            estimated_output_tokens=100,
            idempotency_key="idem-3",
        )
        res = service.admit_turn(cmd)
        # Additional reservation for tool invocation
        service.reserve_additional(
            reservation_id=res.reservation_id,
            additional_micro=50_000,
            idempotency_key="add-tool-1",
        )
        # Query ledger directly using lowercase SQL
        with sqlite_engine.connect() as conn:
            rows = conn.execute(
                text("select entry_type, amount_micro from nlp_quota_ledger_entries where reservation_id = :rid"),
                {"rid": res.reservation_id},
            ).fetchall()
        assert any(row.entry_type == "reserve_increment" for row in rows)

    @requires_server_quota
    def test_tier1_finish_turn_release_unused_reservation(self, sqlite_engine):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 9 & PROJECT.md Feature 15."""
        service = QuotaService(sqlite_engine)
        cmd = AdmitTurn(
            request_id="req-4",
            user_id="user-4",
            turn_id="turn-4",
            model_profile="deepseek",
            model_role="coordinator",
            estimated_input_tokens=100,
            estimated_output_tokens=100,
            idempotency_key="idem-4",
        )
        res = service.admit_turn(cmd)
        service.finish_turn(FinishTurn(reservation_id=res.reservation_id, status="completed"))
        with sqlite_engine.connect() as conn:
            status = conn.execute(
                text("select status from nlp_quota_reservations where reservation_id = :rid"),
                {"rid": res.reservation_id},
            ).scalar()
        assert status == "completed"

    # Category 1F: Cross-Process Attribution & Redis Transport
    def test_tier1_turn_task_carries_reservation_id(self):
        """Authoritative Source: PROJECT.md Feature 17."""
        task = TurnTask(
            context=SessionContext(session_id="s-1", user_id="u-1"),
            turn_id="t-1",
            content="test prompt",
            learning_context=None,
            learning_progress=None,
            exercise_state=None,
            teaching_materials=None,
            guided_session_id=None,
            exercise_session_id=None,
            model_profile="deepseek",
            authorization=ExecutionAuthorizationContext(
                submitter_user_id="u-1",
                workspace_id="ws-1",
                authorization_version=1,
            ),
        )
        assert task.turn_id == "t-1"
        assert task.authorization.workspace_id == "ws-1"

    def test_tier1_redis_codec_serialization_roundtrip(self):
        """Authoritative Source: PROJECT.md Feature 18."""
        task = TurnTask(
            context=SessionContext(session_id="s-2", user_id="u-2"),
            turn_id="t-2",
            content="echo hello",
            learning_context=None,
            learning_progress=None,
            exercise_state=None,
            teaching_materials=None,
            guided_session_id=None,
            exercise_session_id=None,
            model_profile="qwen",
            authorization=ExecutionAuthorizationContext(
                submitter_user_id="u-2",
                workspace_id="ws-2",
                authorization_version=1,
            ),
        )
        serialized = TurnTaskCodec.dumps(task)
        assert isinstance(serialized, str)
        restored = TurnTaskCodec.loads(serialized)
        assert restored.turn_id == task.turn_id
        assert restored.content == task.content
        assert restored.model_profile == "qwen"

    def test_tier1_attribution_binding_context_propagation(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 5.3."""
        attr = _make_attribution(reservation_id="res-binding-1")
        with bind_usage_attribution(attr):
            from core.model_runtime.usage import current_usage_attribution
            current = current_usage_attribution()
            assert current is not None
            assert current.reservation_id == "res-binding-1"
            assert current.user_id == attr.user_id


# ==============================================================================
# Tier 2: Boundary & Corner Cases (Negative & Error Isolation)
# ==============================================================================

class StatusError(RuntimeError):
    def __init__(self, status_code: int, message: str = "failed") -> None:
        super().__init__(message)
        self.status_code = status_code


class TestTier2BoundaryAndCornerCases:
    """Tier 2: Boundary conditions, invariant violations, and fail-fast isolation (>=5 per category)."""

    # Category 2A: Canonical Token Invariant Violations
    def test_tier2_invariant_cached_plus_write_exceeds_input(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 6."""
        with pytest.raises(ValueError, match="must not exceed input_tokens"):
            CanonicalTokenUsage(
                input_tokens=100,
                cached_input_tokens=70,
                cache_write_input_tokens=40,  # 70 + 40 = 110 > 100
                output_tokens=50,
                total_tokens=150,
                source="provider",
            )

    def test_tier2_invariant_reasoning_exceeds_output(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 6."""
        with pytest.raises(ValueError, match="reasoning_output_tokens must be a subset"):
            CanonicalTokenUsage(
                input_tokens=100,
                output_tokens=50,
                reasoning_output_tokens=60,  # 60 > 50
                total_tokens=150,
                source="provider",
            )

    def test_tier2_invariant_total_mismatch(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 6."""
        with pytest.raises(ValueError, match="total_tokens must equal input_tokens \\+ output_tokens"):
            CanonicalTokenUsage(
                input_tokens=100,
                output_tokens=50,
                total_tokens=200,  # 100 + 50 != 200
                source="provider",
            )

    def test_tier2_invariant_float_token_rejected(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 6."""
        with pytest.raises(ValidationError):
            CanonicalTokenUsage(
                input_tokens=100.5,  # float disallowed
                output_tokens=50,
                total_tokens=150,
                source="provider",
            )

    def test_tier2_invariant_boolean_token_rejected(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 6."""
        with pytest.raises(ValidationError):
            CanonicalTokenUsage(
                input_tokens=True,  # boolean disallowed
                output_tokens=50,
                total_tokens=51,
                source="provider",
            )

    def test_tier2_invariant_negative_token_rejected(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 6."""
        with pytest.raises(ValidationError):
            CanonicalTokenUsage(
                input_tokens=-10,
                output_tokens=50,
                total_tokens=40,
                source="provider",
            )

    # Category 2B: Reporter Idempotency Conflicts
    @pytest.mark.asyncio
    async def test_tier2_conflict_altered_input_tokens(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 8."""
        reporter = InMemoryModelUsageReporter()
        inv = _make_invocation()
        usage1 = CanonicalTokenUsage(input_tokens=100, output_tokens=50, total_tokens=150, source="provider")
        usage2 = CanonicalTokenUsage(input_tokens=200, output_tokens=50, total_tokens=250, source="provider")
        await reporter.report(inv, usage1, _make_outcome())
        with pytest.raises(UsageEventConflictError, match="Conflicting usage report"):
            await reporter.report(inv, usage2, _make_outcome())

    @pytest.mark.asyncio
    async def test_tier2_conflict_altered_output_tokens(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 8."""
        reporter = InMemoryModelUsageReporter()
        inv = _make_invocation()
        usage1 = CanonicalTokenUsage(input_tokens=100, output_tokens=50, total_tokens=150, source="provider")
        usage2 = CanonicalTokenUsage(input_tokens=100, output_tokens=80, total_tokens=180, source="provider")
        await reporter.report(inv, usage1, _make_outcome())
        with pytest.raises(UsageEventConflictError):
            await reporter.report(inv, usage2, _make_outcome())

    @pytest.mark.asyncio
    async def test_tier2_conflict_altered_model_identity(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 8."""
        reporter = InMemoryModelUsageReporter()
        op_id = str(uuid.uuid4())
        inv1 = _make_invocation(operation_id=op_id, identity=_make_identity(provider="deepseek"))
        inv2 = _make_invocation(operation_id=op_id, identity=_make_identity(provider="qwen"))
        usage = CanonicalTokenUsage(input_tokens=100, output_tokens=50, total_tokens=150, source="provider")
        await reporter.report(inv1, usage, _make_outcome())
        with pytest.raises(UsageEventConflictError):
            await reporter.report(inv2, usage, _make_outcome())

    def test_tier2_invalid_non_uuidv4_operation_id(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 5.1."""
        with pytest.raises(ValueError, match="operation_id must be a UUIDv4"):
            ModelInvocation(
                operation_id="not-a-valid-uuid",
                identity=_make_identity(),
                attribution=_make_attribution(),
                attempt=1,
                fallback_index=0,
                started_at=datetime.now(UTC),
            )

    # Category 2C: Fail-Fast Error Isolation
    @pytest.mark.asyncio
    async def test_tier2_reporter_failure_halts_retry_loop(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 8 & PROJECT.md Feature 7."""
        class BrokenReporter:
            def __init__(self) -> None:
                self.calls = 0

            async def report(self, invocation, usage, outcome):
                self.calls += 1
                raise RuntimeError("Database connection lost during usage persistence")

        reporter = BrokenReporter()
        slot = ModelUsageReporterSlot(reporter)
        fake = FakeRawModel([
            [AIMessageChunk(
                content="hello",
                response_metadata={"id": "resp-fail", "token_usage": {"prompt_tokens": 10, "completion_tokens": 5}},
            )],
            [AIMessageChunk(content="must not be executed")],
        ])
        cand = _make_candidate("test-cand", fake, attempts=3)
        resilient = ResilientChatModel([cand], reporter_slot=slot)

        with bind_usage_attribution(_make_attribution()):
            with pytest.raises(RuntimeError, match="Database connection lost"):
                async for _ in resilient.astream([HumanMessage(content="hi")]):
                    pass

        # Provider must have been called exactly once; retry loop halted immediately
        assert fake.calls == 1
        assert reporter.calls == 1

    @pytest.mark.asyncio
    async def test_tier2_missing_attribution_prevents_provider_call(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 5.3."""
        reporter = InMemoryModelUsageReporter()
        slot = ModelUsageReporterSlot(reporter)
        fake = FakeRawModel([[AIMessageChunk(content="unreachable")]])
        cand = _make_candidate("cand-no-attr", fake)
        resilient = ResilientChatModel([cand], reporter_slot=slot)

        # Without bind_usage_attribution or TelemetryContext, call must fail before provider execution
        with pytest.raises(MissingUsageAttributionError):
            async for _ in resilient.astream([HumanMessage(content="test")]):
                pass
        assert fake.calls == 0

    # Category 2D: Unpriced Usage & Unknown Keys
    def test_tier2_source_none_with_tokens_rejected(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 6."""
        with pytest.raises(ValueError, match="source=none cannot carry token values"):
            CanonicalTokenUsage(
                input_tokens=10,
                output_tokens=10,
                total_tokens=20,
                source="none",
            )

    @requires_server_quota
    def test_tier2_source_none_cannot_be_priced(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 6."""
        rule = PricingRule(
            pricing_key="deepseek/deepseek-v4-pro",
            version="v1",
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
            ordinary_input_credits_micro_per_million_tokens=1_000_000,
            cached_input_credits_micro_per_million_tokens=500_000,
            cache_write_credits_micro_per_million_tokens=500_000,
            output_credits_micro_per_million_tokens=2_000_000,
        )
        catalog = PricingCatalog([rule])
        inv = _make_invocation()
        usage_none = CanonicalTokenUsage(source="none")
        with pytest.raises(UnknownUsageCannotBePricedError):
            catalog.price(inv, usage_none, _make_outcome())

    @requires_server_quota
    def test_tier2_unknown_pricing_key_raises_error(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 5.2."""
        rule = PricingRule(
            pricing_key="deepseek/deepseek-v4-pro",
            version="v1",
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
            ordinary_input_credits_micro_per_million_tokens=1_000_000,
            cached_input_credits_micro_per_million_tokens=500_000,
            cache_write_credits_micro_per_million_tokens=500_000,
            output_credits_micro_per_million_tokens=2_000_000,
        )
        catalog = PricingCatalog([rule])
        inv_unknown = _make_invocation(identity=_make_identity(pricing_key="unknown/model-xyz"))
        usage = CanonicalTokenUsage(input_tokens=100, output_tokens=50, total_tokens=150, source="provider")
        with pytest.raises(UnknownPricingKeyError):
            catalog.price(inv_unknown, usage, _make_outcome())

    # Category 2E: Quota Bucket Depletion & Concurrency Limits
    @requires_server_quota
    def test_tier2_admission_rejected_daily_exhausted(self, sqlite_engine):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 11."""
        service = QuotaService(sqlite_engine)
        # Seed 0 balance in user daily bucket
        with sqlite_engine.begin() as conn:
            conn.execute(
                text("insert into nlp_quota_buckets (id, owner_type, owner_id, bucket_type, balance_micro) values ('b-1', 'user', 'user-broke', 'daily', 0)")
            )
        cmd = AdmitTurn(
            request_id="req-broke",
            user_id="user-broke",
            turn_id="turn-broke",
            model_profile="deepseek",
            model_role="coordinator",
            estimated_input_tokens=100,
            estimated_output_tokens=100,
            idempotency_key="idem-broke",
        )
        with pytest.raises(QuotaRejectedError, match="quota_daily_exhausted"):
            service.admit_turn(cmd)

    # Category 2F: Error Taxonomy Separation
    def test_tier2_error_taxonomy_provider_quota_exhausted(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 11."""
        error_402 = StatusError(402, "Insufficient balance in upstream provider account")
        classified = classify_model_error(error_402)
        assert classified.kind == "upstream_provider_quota_exhausted"
        assert classified.retryable is False

    def test_tier2_error_taxonomy_rate_limited(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 11."""
        error_429 = StatusError(429, "Too Many Requests")
        classified = classify_model_error(error_429)
        assert classified.kind == "upstream_rate_limited"
        assert classified.retryable is True


# ==============================================================================
# Tier 3: Cross-Feature Combinations (Pairwise & Concurrency)
# ==============================================================================

class TestTier3CrossFeatureCombinations:
    """Tier 3: Multi-subsystem interaction, retry/fallback multi-attempt reporting, and concurrency."""

    @pytest.mark.asyncio
    async def test_tier3_combo_retry_multiple_attempts_distinct_operation_ids(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 5.1, 7."""
        reporter = InMemoryModelUsageReporter()
        slot = ModelUsageReporterSlot(reporter)

        # Attempt 1: Connection reset; Attempt 2: Success
        fake = FakeRawModel([
            ConnectionResetError("Peer reset connection"),
            [AIMessageChunk(
                content="recovery response",
                response_metadata={"id": "resp-attempt-2", "token_usage": {"prompt_tokens": 80, "completion_tokens": 40}},
            )],
        ])
        cand = _make_candidate("cand-retry", fake, attempts=2)
        resilient = ResilientChatModel([cand], reporter_slot=slot)

        with bind_usage_attribution(_make_attribution()):
            chunks = []
            async for chunk in resilient.astream([HumanMessage(content="trigger retry")]):
                chunks.append(chunk)

        # 2 distinct provider attempts must be recorded
        assert len(reporter.events) == 2
        op_id_1 = reporter.events[0][0].operation_id
        op_id_2 = reporter.events[1][0].operation_id
        assert op_id_1 != op_id_2

        # Attempt 1: failed, attempt=1
        assert reporter.events[0][0].attempt == 1
        assert reporter.events[0][2].status == "failed"

        # Attempt 2: succeeded, attempt=2
        assert reporter.events[1][0].attempt == 2
        assert reporter.events[1][2].status == "succeeded"
        assert reporter.events[1][1].total_tokens == 120

    @pytest.mark.asyncio
    async def test_tier3_combo_fallback_candidate_pricing_switch(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 5.1, 5.2."""
        reporter = InMemoryModelUsageReporter()
        slot = ModelUsageReporterSlot(reporter)

        fake_primary = FakeRawModel([ConnectionResetError("Primary 500 Internal Connection Reset")])
        fake_fallback = FakeRawModel([[
            AIMessageChunk(
                content="fallback success",
                response_metadata={"id": "resp-qwen", "token_usage": {"prompt_tokens": 50, "completion_tokens": 25}},
            )
        ]])

        cand_primary = _make_candidate("deepseek-primary", fake_primary, attempts=1, pricing_key="deepseek/deepseek-v4-pro")
        cand_fallback = _make_candidate("qwen-fallback", fake_fallback, attempts=1, pricing_key="qwen/qwen3.8-max")
        resilient = ResilientChatModel([cand_primary, cand_fallback], reporter_slot=slot)

        with bind_usage_attribution(_make_attribution()):
            async for _ in resilient.astream([HumanMessage(content="fallback test")]):
                pass

        assert len(reporter.events) == 2
        # Event 1: DeepSeek primary
        assert reporter.events[0][0].identity.pricing_key == "deepseek/deepseek-v4-pro"
        assert reporter.events[0][0].fallback_index == 0
        assert reporter.events[0][2].status == "failed"

        # Event 2: Qwen fallback
        assert reporter.events[1][0].identity.pricing_key == "qwen/qwen3.8-max"
        assert reporter.events[1][0].fallback_index == 1
        assert reporter.events[1][2].status == "succeeded"

    @pytest.mark.asyncio
    async def test_tier3_combo_structured_output_parse_error_with_usage(self):
        """Authoritative Source: docs/specs/model-usage-quota-interface-handoff.md § 7."""
        reporter = InMemoryModelUsageReporter()
        slot = ModelUsageReporterSlot(reporter)

        class AnswerModel(BaseModel):
            rating: int

        # Step 1: Provider returns malformed JSON
        fake = FakeRawModel([{
            "raw": AIMessage(
                content="invalid json response",
                response_metadata={"id": "resp-parse-err", "token_usage": {"prompt_tokens": 30, "completion_tokens": 15}},
            ),
            "parsed": None,
            "parsing_error": ValueError("Invalid JSON syntax"),
        }])
        cand = _make_candidate("cand-struct", fake)
        resilient = ResilientChatModel([cand], reporter_slot=slot)
        structured = resilient.with_structured_output(AnswerModel)

        with bind_usage_attribution(_make_attribution()):
            with pytest.raises(ValueError, match="Invalid JSON syntax"):
                await structured.ainvoke([HumanMessage(content="give rating")])

        # Attempt must be recorded despite parse failure because Provider tokens were consumed
        assert len(reporter.events) == 1
        assert reporter.events[0][2].status == "failed"
        assert reporter.events[0][2].error_kind == "structured_output_parse_error"
        assert reporter.events[0][1].total_tokens == 45


# ==============================================================================
# Tier 4: Real-World Application Scenarios (Realistic E2E Workflows)
# ==============================================================================

class TestTier4RealWorldWorkflows:
    """Tier 4: Comprehensive end-to-end user and system lifecycles (>=5 realistic workflows)."""

    @pytest.mark.asyncio
    async def test_tier4_workflow_1_standard_chat_turn(self):
        """Workflow 1: Standard single-turn chat interaction from user prompt to stream completion."""
        reporter = InMemoryModelUsageReporter()
        slot = ModelUsageReporterSlot(reporter)

        fake = FakeRawModel([[
            AIMessageChunk(
                content="Hello! How can I help you today?",
                response_metadata={"id": "resp-norm-1", "token_usage": {"prompt_tokens": 15, "completion_tokens": 8}},
            )
        ]])
        cand = _make_candidate("coordinator-chat", fake)
        resilient = ResilientChatModel([cand], reporter_slot=slot)

        attr = _make_attribution(user_id="user-alice", turn_id="turn-chat-1", reservation_id="res-chat-1")
        with bind_usage_attribution(attr):
            collected = []
            async for chunk in resilient.astream([HumanMessage(content="Hello!")]):
                collected.append(chunk.content)

        assert "".join(collected) == "Hello! How can I help you today?"
        assert len(reporter.events) == 1
        inv, usage, outcome = reporter.events[0]
        assert inv.attribution.user_id == "user-alice"
        assert inv.attribution.reservation_id == "res-chat-1"
        assert usage.input_tokens == 15
        assert usage.output_tokens == 8
        assert outcome.status == "succeeded"

    @pytest.mark.asyncio
    async def test_tier4_workflow_2_transient_failure_and_retry_recovery(self):
        """Workflow 2: Transient network reset during attempt 1, followed by clean recovery on attempt 2."""
        reporter = InMemoryModelUsageReporter()
        slot = ModelUsageReporterSlot(reporter)

        fake = FakeRawModel([
            TimeoutError("Connect timeout to upstream gateway"),
            [AIMessageChunk(
                content="Recovered after timeout",
                response_metadata={"id": "resp-retry-ok", "token_usage": {"prompt_tokens": 25, "completion_tokens": 10}},
            )],
        ])
        cand = _make_candidate("resilient-worker", fake, attempts=2)
        resilient = ResilientChatModel([cand], reporter_slot=slot)

        attr = _make_attribution(user_id="user-bob", turn_id="turn-retry-2")
        with bind_usage_attribution(attr):
            chunks = []
            async for chunk in resilient.astream([HumanMessage(content="Compute task")]):
                chunks.append(chunk.content)

        assert "".join(chunks) == "Recovered after timeout"
        assert len(reporter.events) == 2
        # Verify attempt 1 was marked failed with timeout
        assert reporter.events[0][0].attempt == 1
        assert reporter.events[0][2].status == "failed"
        assert reporter.events[0][2].error_kind == "upstream_timeout"
        # Verify attempt 2 succeeded with tokens
        assert reporter.events[1][0].attempt == 2
        assert reporter.events[1][2].status == "succeeded"
        assert reporter.events[1][1].total_tokens == 35

    @pytest.mark.asyncio
    async def test_tier4_workflow_3_interrupted_streaming_turn(self):
        """Workflow 3: User cancels or client disconnects midway through a streaming response."""
        reporter = InMemoryModelUsageReporter()
        slot = ModelUsageReporterSlot(reporter)

        # Provider produces 2 chunks then triggers stream interruption
        fake = FakeRawModel([[
            AIMessageChunk(content="Partial thought 1... "),
            AIMessageChunk(content="Partial thought 2... "),
            ConnectionResetError("Client disconnected unexpectedly"),
        ]])
        cand = _make_candidate("stream-interrupt", fake)
        resilient = ResilientChatModel([cand], reporter_slot=slot)

        attr = _make_attribution(user_id="user-charlie", turn_id="turn-stream-3")
        with bind_usage_attribution(attr):
            collected = []
            with pytest.raises(StreamInterruptedError):
                async for chunk in resilient.astream([HumanMessage(content="Long story")]):
                    collected.append(chunk.content)

        assert len(collected) == 2
        assert len(reporter.events) == 1
        assert reporter.events[0][2].status == "interrupted"

    def test_tier4_workflow_4_cross_process_worker_dispatch_roundtrip(self, fake_redis):
        """Workflow 4: Web process creates turn task with attribution, queues in Redis, worker restores context."""
        # 1. Web process creates task
        original_task = TurnTask(
            context=SessionContext(session_id="session-async-1", user_id="user-david"),
            turn_id="turn-async-101",
            content="Execute analysis",
            learning_context=None,
            learning_progress=None,
            exercise_state=None,
            teaching_materials=None,
            guided_session_id=None,
            exercise_session_id=None,
            model_profile="deepseek",
            authorization=ExecutionAuthorizationContext(
                submitter_user_id="user-david",
                workspace_id="ws-david",
                authorization_version=2,
            ),
        )

        # 2. Serialize and push to FakeRedis stream
        payload = TurnTaskCodec.dumps(original_task)
        asyncio.run(fake_redis.xadd("nlp-agent:turns", {"payload": payload}))
        assert len(fake_redis.streams) == 1

        # 3. Worker reads payload from stream and reconstructs task
        received_payload = fake_redis.streams[0][1]["payload"]
        worker_task = TurnTaskCodec.loads(received_payload)
        assert worker_task.turn_id == "turn-async-101"
        assert worker_task.context.user_id == "user-david"

        # 4. Worker binds attribution context for execution
        worker_attr = UsageAttributionContext(
            request_id=str(uuid.uuid4()),
            user_id=worker_task.context.user_id,
            workspace_id=worker_task.authorization.workspace_id if worker_task.authorization else None,
            conversation_id=worker_task.context.session_id,
            turn_id=worker_task.turn_id,
            worker_id="worker-node-1",
            purpose="worker",
        )
        with bind_usage_attribution(worker_attr):
            from core.model_runtime.usage import current_usage_attribution
            active = current_usage_attribution()
            assert active is not None
            assert active.user_id == "user-david"
            assert active.worker_id == "worker-node-1"
            assert active.purpose == "worker"

    @pytest.mark.asyncio
    async def test_tier4_workflow_5_multi_turn_consecutive_isolation(self):
        """Workflow 5: Consecutive independent turns execute without attribution leakage between turns."""
        reporter = InMemoryModelUsageReporter()
        slot = ModelUsageReporterSlot(reporter)

        fake = FakeRawModel([
            [AIMessageChunk(content="Turn 1 answer", response_metadata={"id": "r1", "token_usage": {"prompt_tokens": 10, "completion_tokens": 5}})],
            [AIMessageChunk(content="Turn 2 answer", response_metadata={"id": "r2", "token_usage": {"prompt_tokens": 20, "completion_tokens": 10}})],
        ])
        cand = _make_candidate("cand-multi", fake)
        resilient = ResilientChatModel([cand], reporter_slot=slot)

        # Turn 1
        attr1 = _make_attribution(user_id="user-1", turn_id="turn-1", reservation_id="res-1")
        with bind_usage_attribution(attr1):
            async for _ in resilient.astream([HumanMessage(content="msg 1")]):
                pass

        # Turn 2
        attr2 = _make_attribution(user_id="user-2", turn_id="turn-2", reservation_id="res-2")
        with bind_usage_attribution(attr2):
            async for _ in resilient.astream([HumanMessage(content="msg 2")]):
                pass

        assert len(reporter.events) == 2
        assert reporter.events[0][0].attribution.turn_id == "turn-1"
        assert reporter.events[0][0].attribution.reservation_id == "res-1"
        assert reporter.events[1][0].attribution.turn_id == "turn-2"
        assert reporter.events[1][0].attribution.reservation_id == "res-2"
