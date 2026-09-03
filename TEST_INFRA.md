# Test Infrastructure Specification: Model Usage Metering & Token Quota Management

## 1. Overview & Purpose
This document establishes the end-to-end testing infrastructure, methodology, tier hierarchy, and verification criteria for the **Model Usage Metering and Token Quota Management** subsystem on branch `feat/quota-management-impl`.

The testing framework guarantees end-to-end integrity across both sides of the architecture:
1. **Producer Side (`core/model_runtime/`)**: Canonical token contracts, immutable invocations, telemetry attribution, attempt-level tracking, and fail-fast reporter hooks.
2. **Consumer Side (`server/quota/`)**: Relational database models, multi-rate pricing catalog, durable reporter, admission reservations, dynamic additional reservations, settlement, and release.
3. **Gateway & Transport Boundary (`gateway/`, `server/worker/`)**: Cross-process attribution propagation, Redis task serialization, and worker execution attribution inheritance.

---

## 2. Testing Philosophy & Core Principles

### 2.1 Opaque-Box & Public Interface Verification
Tests interact **strictly through public contracts and interfaces** defined in `PROJECT.md` and `docs/specs/model-usage-quota-interface-handoff.md`:
- `admit_turn(user_id, workspace_id, model_profile, estimated_input_tokens)`
- `ModelUsageReporter.report(invocation, usage, outcome)`
- `reserve_additional(reservation_id, additional_micro, idempotency_key)`
- `finish_turn(reservation_id, status)`
- `TurnTaskCodec.dumps()` / `TurnTaskCodec.loads()`
- `CanonicalTokenUsage`, `ModelInvocation`, `UsageAttributionContext`

Tests avoid asserting against private runtime internal variables or implementation hacks. If the internal implementation refactors, valid tests remain green if and only if public behavior and invariants hold.

### 2.2 Requirement-Driven & Authoritative Derivation
Every test case maps to specific requirements in `ORIGINAL_REQUEST.md` (R1–R5) and `PROJECT.md` (Features 1–22, Acceptance Criteria 1–16). Expected outcomes are derived from documented mathematical rules and specifications:
- **Token Invariants**:
  - `cached_input_tokens + cache_write_input_tokens <= input_tokens`
  - `reasoning_output_tokens <= output_tokens`
  - `total_tokens == input_tokens + output_tokens`
  - All token counts are strict non-negative integers; `source="none"` enforces 0 across all token counts.
- **Pricing Calculation**:
  - `ordinary_input_tokens = input_tokens - cached_input_tokens - cache_write_input_tokens`
  - `ordinary_output_tokens = output_tokens - reasoning_output_tokens` (if reasoning rate specified)
  - `cost_micro = ceil(ordinary_input * ordinary_rate / 1,000,000) + ceil(cached_input * cached_rate / 1,000,000) + ...`
  - `source="none"` must raise `UnknownUsageCannotBePricedError` or enter pending state; it is never free 0 tokens.
- **Deduplication & Idempotency**:
  - Identical `operation_id` with identical payload = no-op return.
  - Identical `operation_id` with conflicting payload = raises `UsageEventConflictError`.
- **Fail-Fast Error Isolation**:
  - If `reporter.report()` raises an error, runtime re-raises `_ReporterFailure` / RuntimeError, halting retries/fallbacks and preventing unpaid duplicate provider requests.

### 2.3 Progressive Testability & Milestones
Because implementation proceeds across Milestones M1–M4, the test suite supports progressive testability:
- Producer side contracts (`core.model_runtime.usage`, `CanonicalTokenUsage` invariants, error classification) are immediately verifiable.
- Consumer quota interfaces (`server.quota`) are verified as soon as M1–M4 components are wired.
- When run during intermediate development, tests for unmerged milestones are cleanly isolated or marked so the test runner maintains clear status without collection crashes.

### 2.4 Isolation, Determinism & Zero Network Dependencies
- **Database Fixture**: In-memory SQLite with `StaticPool` (`sqlite:///:memory:`, `check_same_thread=False`) creating all quota tables per test, ensuring sub-second execution, transaction isolation, and zero persistent state leakage.
- **Transport Fixture**: In-memory `FakeRedis` capturing streams, subscriptions, and key-value locks without requiring external Redis daemon.
- **Timezone Awareness**: All timestamps enforce UTC timezone awareness.

### 2.5 Adversarial Hardening
Adversarial test cases verify resilience against invalid types, SQL casing violations, and corrupted states:
1. **Lowercase SQL Rule**: Zero uppercase SQL keywords, table names, or column names in queries, schemas, or models.
2. **Type Violations**: Strict rejection of boolean `True`/`False`, floats (`10.5`), negative numbers, and string values for token counts.
3. **Boundary Values**: 0 tokens, max context tokens (e.g. 1,000,000), single token increments, credit starvation (0 balance).
4. **Error Distinction**: Strict separation of upstream provider quota exhaustion (`upstream_provider_quota_exhausted`) from user subscription exhaustion (`quota_daily_exhausted`).

---

## 3. Four-Tier Test Architecture

| Tier | Category | Purpose | Minimum Coverage Threshold |
|---|---|---|---|
| **Tier 1** | Feature Coverage | Happy path & functional baseline verification for each core feature | >= 5 test cases per feature category |
| **Tier 2** | Boundary & Corner Cases | Negative testing, invariant checks, edge cases, error isolation | >= 5 test cases per boundary category |
| **Tier 3** | Cross-Feature Combinations | Pairwise interaction between subsystems (admission, reporting, transport) | All key pairwise combinations |
| **Tier 4** | Real-World Application Scenarios | Full end-to-end realistic lifecycles and user workflows | >= 5 complete workflows |

---

## 4. Feature Inventory Mapping to Tiers 1–4

### 4.1 Tier 1: Feature Coverage (Smoke & Happy Path)
*Threshold: >= 5 test cases per feature category*

1. **Category 1A: Quota Schema & Relational Models (F1, F3, F4)**
   - Creation of all 10 quota tables in SQLite in-memory engine.
   - Verification of strictly lowercase table names (`nlp_pricing_rules`, `nlp_usage_events`, `nlp_quota_policies`, `nlp_quota_policy_bindings`, `nlp_quota_buckets`, `nlp_quota_concurrency_locks`, `nlp_quota_reservations`, `nlp_quota_ledger_entries`, `nlp_quota_grants`, `nlp_quota_adjustments`).
   - Insertion and retrieval of valid pricing rules.
   - Insertion and retrieval of valid attempt usage events with UUIDv4 `operation_id`.
   - Insertion and retrieval of quota reservations with status `reserved`.

2. **Category 1B: Multi-Rate Token Pricing Engine (F2)**
   - Pricing standard non-cached prompt and output.
   - Pricing prompt with cached input token discount.
   - Pricing prompt with cache-write input tokens.
   - Pricing output with distinct reasoning tokens.
   - Pricing multi-provider pricing keys (`deepseek/deepseek-v4-pro`, `qwen/qwen3.8-max`).

3. **Category 1C: Durable Usage Reporter (F5, F6)**
   - Single successful attempt report persisted to database.
   - Replay of identical report with same `operation_id` returns cleanly (idempotent no-op).
   - Attempt records correctly store `attempt=1`, `fallback_index=0`, and UTC timestamps.
   - Attempt records correctly link to `reservation_id`.
   - Attempt records store outcome status `succeeded` and finish reason.

4. **Category 1D: Call Admission & Token Estimation (F12, F13)**
   - `admit_turn` succeeds with valid user having positive credit balance.
   - `admit_turn` creates active reservation and concurrency lock.
   - Conservative estimation fallback applied when `estimated_input_tokens` is None (never treated as 0).
   - `admit_turn` calculates estimated micro-credits based on active pricing key.
   - `admit_turn` respects workspace quota binding when workspace_id is provided.

5. **Category 1E: Dynamic Additional Reservation & Settlement (F14, F15)**
   - `reserve_additional` succeeds when user has sufficient available credits.
   - `reserve_additional` writes `entry_type="reserve_increment"` ledger entry.
   - `finish_turn` releases remaining reserved credits (`entry_type="release"`).
   - `finish_turn` transitions reservation status to `completed`.
   - `finish_turn` releases the concurrency lock.

6. **Category 1F: Cross-Process Attribution & Redis Serialization (F17, F18, F19, F20)**
   - `TurnTask` carries `reservation_id` and `request_id`.
   - `TurnTaskCodec.dumps()` serializes `reservation_id`.
   - `TurnTaskCodec.loads()` restores `reservation_id` from JSON.
   - `TurnTaskCodec.loads()` backward compatibility with legacy payloads missing `reservation_id`.
   - Context propagation via `bind_usage_attribution()` preserves `reservation_id` in current execution context.

---

### 4.2 Tier 2: Boundary & Corner Cases (Negative, Invariants & Error Isolation)
*Threshold: >= 5 test cases per boundary category*

1. **Category 2A: Canonical Token Invariant Violations**
   - Negative test: `cached_input_tokens + cache_write_input_tokens > input_tokens` raises `ValueError`.
   - Negative test: `reasoning_output_tokens > output_tokens` raises `ValueError`.
   - Negative test: `total_tokens != input_tokens + output_tokens` raises `ValueError`.
   - Negative test: Float or boolean token values rejected by strict typing.
   - Negative test: Negative integer token values rejected.

2. **Category 2B: Reporter Idempotency Conflicts & Replay Guards**
   - Negative test: Same `operation_id` with altered `input_tokens` raises `UsageEventConflictError`.
   - Negative test: Same `operation_id` with altered `output_tokens` raises `UsageEventConflictError`.
   - Negative test: Same `operation_id` with altered `provider_model` raises `UsageEventConflictError`.
   - Negative test: Same `operation_id` with altered `user_id` raises `UsageEventConflictError`.
   - Negative test: Invalid non-UUIDv4 `operation_id` raises validation error.

3. **Category 2C: Fail-Fast Error Isolation & Provider Protection**
   - Reporter DB failure halts retry loop; does not trigger secondary provider attempts.
   - Reporter failure on streaming completion re-raises exception to terminate stream.
   - Reporter failure during structured output preserves exception without secondary call.
   - Reporter failure on provider error preserves original provider failure while logging reporter issue.
   - Absence of attribution context raises `MissingUsageAttributionError` before provider call.

4. **Category 2D: Unpriced & Unknown Usage Handling (F9)**
   - `source="none"` with non-zero tokens rejected by `CanonicalTokenUsage` validator.
   - `source="none"` with zero tokens rejected by `PricingCatalog.price()` with `UnknownUsageCannotBePricedError`.
   - `source="none"` usage saved with status `pending` for offline reconciliation.
   - Unknown `pricing_key` raises `UnknownPricingKeyError` without defaulting to 0 cost.
   - Out-of-date pricing rule (completed_at outside effective window) raises error.

5. **Category 2E: Quota Bucket Depletion & Concurrency Limits (F12, F16)**
   - User with 0 daily balance rejected with `quota_daily_exhausted`.
   - User with 0 weekly balance rejected with `quota_weekly_exhausted`.
   - Second concurrent turn for same user/workspace rejected with `concurrency_limit`.
   - Additional reservation exceeding remaining balance fails and rolls back atomically.
   - Rejection leaves database in clean state (no orphaned reservations or phantom locks).

6. **Category 2F: Error Taxonomy Separation (F16)**
   - HTTP 402 from Provider classified as `upstream_provider_quota_exhausted` (Infrastructure).
   - User balance exhaustion classified as `quota_daily_exhausted` (User Domain).
   - Upstream rate limit classified as `upstream_rate_limited` with retry eligibility.
   - Upstream context length exceeded classified as `upstream_context_length_exceeded` without retry.
   - Distinction preserved in database `error_kind` column.

---

### 4.3 Tier 3: Cross-Feature Combinations (Pairwise & Concurrency)
*Target: Pairwise combinations across subsystems*

1. **Combo 1: Admission + Multi-Rate Pricing + Durable Settlement**
   - Sequence: `admit_turn` reserves conservative estimate -> Reporter prices actual multi-rate tokens (`cached_input`, `output`) -> `finish_turn` calculates actual vs reserved variance and releases difference.
2. **Combo 2: Multi-Attempt Retry Lifecycle (Attempt 1 Fail -> Attempt 2 Success)**
   - Sequence: `admit_turn` -> Attempt 1 fails (`upstream_timeout`, `operation_id_1`, partial tokens) -> Attempt 2 succeeds (`operation_id_2`, full tokens) -> Both attempts persisted with distinct `operation_id`s -> Settle total tokens across attempts.
3. **Combo 3: Candidate Model Fallback (Primary -> Fallback Candidate)**
   - Sequence: Candidate 1 (`deepseek/deepseek-v4-pro`) fails -> Fallback to Candidate 2 (`qwen/qwen3.8-max`) -> Attempt 1 logged with DeepSeek pricing key -> Attempt 2 logged with Qwen pricing key -> Correct separate pricing applied per attempt.
4. **Combo 4: Dynamic Additional Reservation + Boundary Depletion**
   - Sequence: Turn admitted -> Worker tool requests `reserve_additional` -> Balance sufficient -> Second tool requests `reserve_additional` exceeding remaining balance -> Second request fails -> Prior reservation remains valid -> Turn finishes cleanly.
5. **Combo 5: Multi-Tenant Concurrency Lock Isolation**
   - Sequence: User A turn locks User A -> User B turn simultaneously succeeds without blocking -> Second turn for User A blocked -> User A finishes -> Subsequent turn for User A admitted.
6. **Combo 6: Distributed Redis Worker Context Roundtrip**
   - Sequence: Web creates reservation -> Encodes `reservation_id` in `TurnTask` -> FakeRedis transports task -> Worker deserializes task -> Binds attribution with `purpose="worker"`, `worker_id` -> Invocation reported under worker attribution with matching `reservation_id`.

---

### 4.4 Tier 4: Real-World Application Scenarios (Realistic E2E Workflows)
*Target: >= 5 complete realistic workflows*

1. **Workflow 1: Standard Single-Turn Interaction**
   - Full lifecycle of a standard chat turn: User initiates request -> System estimates 250 input tokens -> Admits turn and creates reservation -> Model streams response (120 ordinary input, 80 cached input, 150 output tokens) -> Reporter persists Attempt with UTC timestamp -> Turn completes -> Unused reserved micro-credits refunded -> Concurrency lock cleared.
2. **Workflow 2: Transient Provider Error & Successful Retry**
   - Complete retry journey: Web admits turn -> Provider attempt 1 encounters connection reset (`upstream_connection_error`) after receiving 50 tokens -> Attempt 1 reported to DB -> Resilient runtime executes attempt 2 -> Attempt 2 succeeds with 200 tokens -> Attempt 2 reported to DB with new `operation_id` -> Ledger reflects total token consumption -> Residual reservation released.
3. **Workflow 3: Multi-Tool Turn with Dynamic Additional Reservation**
   - Agentic workflow with expensive capability: User prompts for complex web analysis -> Coordinator admits turn -> Coordinator invokes VLM / Search tool -> Tool calls `reserve_additional` for 50,000 micro-credits -> Ledger records `reserve_increment` -> Tool executes -> Final model synthesis completes -> Turn finishes with comprehensive multi-rate settlement.
4. **Workflow 4: User-Cancelled / Interrupted Streaming Turn**
   - Interruption handling: User starts long generation -> 40 tokens emitted to client -> Client disconnects or aborts -> Runtime catches cancellation -> Emits `interrupted` / `cancelled` invocation outcome -> Reporter reliably records tokens consumed prior to abort -> `finish_turn(status="interrupted")` releases reservation.
5. **Workflow 5: Distributed Worker Task Execution via Redis**
   - Asynchronous worker flow: Web process creates turn task with `request_id` and `reservation_id` -> Task submitted to Redis stream -> Worker runtime claims task -> Unpacks attribution context -> Executes subagent loop with `purpose="worker"` -> Model calls report usage tied to parent `reservation_id` -> Turn completed on worker -> Ledger verified in database.

---

## 5. Test Fixtures & Environment Setup

```python
@pytest.fixture
def sqlite_quota_engine():
    """In-memory SQLite engine with StaticPool for fast, isolated tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Create all lowercase tables
    ...
    yield engine
    engine.dispose()

@pytest.fixture
def fake_redis():
    """In-memory FakeRedis capturing streams, keys, and pub/sub."""
    return FakeRedis()
```

---

## 6. Execution Command & Verification Criteria

### 6.1 Test Runner Commands
```powershell
# Run the complete E2E Quota test suite
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_quota_e2e_suite.py -v

# Run with coverage report
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_quota_e2e_suite.py -v --tb=short

# Run linter on the test suite
.\.venv\Scripts\python.exe -m ruff check tests/e2e/test_quota_e2e_suite.py
```

### 6.2 Acceptance Criteria for Test Suite
1. **Compilation & Syntax**: 0 errors on Python 3.13.
2. **Lint Cleanliness**: 0 errors from `ruff check`.
3. **Execution Speed**: Full suite executes in under 10 seconds.
4. **Pass Rate**: 100% pass when corresponding milestone code is present.
5. **Zero Side Effects**: No temporary database files left on disk, no leaked network ports.
