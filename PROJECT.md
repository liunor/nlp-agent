# Project: Model Usage Metering and Token Quota Management

## Architecture
The system integrates LLM Model Runtime with Token Quota Management across Web and Worker processes:
1. **Producer Side (`core/model_runtime/`)**:
   - `ModelInvocation` with UUIDv4 `operation_id`, UTC timestamps, attempt & fallback indices.
   - `CanonicalTokenUsage` enforcing strict invariants (`cached + cache_write <= input`, `reasoning <= output`, `total == input + output`).
   - `UsageAttributionContext` binding `request_id`, `user_id`, `workspace_id`, `turn_id`, `reservation_id`, `worker_id`, `purpose`.
   - `ModelUsageReporter` async protocol calling `report(invocation, usage, outcome)`.
2. **Consumer Side (`server/quota/`)**:
   - Relational ORM models (`server/quota/models.py`) adhering to strictly lowercase SQL keywords, tables, and columns.
   - `PricingCatalog` & `PricingRule` (`server/quota/pricing.py`) calculating micro-credits without double-charging cached or reasoning tokens.
   - `DurableModelUsageReporter` (`server/quota/reporting.py`) with atomic DB transaction persistence, `operation_id` idempotency, and `_ReporterFailure` isolation.
   - `QuotaService` (`server/quota/service.py`) managing concurrency locks, daily/weekly buckets, admission reservations, dynamic additional reservations (`reserve_additional`), settlement, and release.
   - `bootstrap.py` lifecycle hooks configuring the global reporter during startup and cleaning up on shutdown.
3. **Gateway & Cross-Process Transport (`gateway/`, `server/worker/`, `server/tools/`)**:
   - `TurnTask` and `TurnTaskCodec` in `gateway/redis_transport.py` serializing `reservation_id` and `request_id`.
   - `gateway/turn_execution.py` binding `UsageAttributionContext` with `task.reservation_id`.
   - `server/worker/runtime.py` injecting `configure_usage_reporter()` on worker startup.
   - `server/tools/worker_tool.py` propagating `reservation_id` into worker subagents with `purpose="worker"`.

## Feature Inventory
Every feature from the Survey phase appears here with its assigned milestone.
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Quota Relational Models | 10 SQLAlchemy ORM models (`nlp_pricing_rules`, `nlp_usage_events`, `nlp_quota_policies`, `nlp_quota_policy_bindings`, `nlp_quota_buckets`, `nlp_quota_concurrency_locks`, `nlp_quota_reservations`, `nlp_quota_ledger_entries`, `nlp_quota_grants`, `nlp_quota_adjustments`) with strictly lowercase SQL keywords and column names. | M1 | R1 |
| 2 | Multi-Rate Token Pricing | `PricingCatalog` calculating micro-credits across disjoint token partitions (`ordinary_input`, `cached_input`, `cache_write_input`, `output`, `reasoning_output`) without double charging. | M1 | R1 |
| 3 | Quota Database Migrations | Alembic migrations establishing quota schema, unique indexes on `operation_id` and idempotency keys, and lowercase check constraints. | M1 | R1 |
| 4 | SQLite Test Fixtures | Transactional in-memory SQLite fixture setup (`sqlite:///:memory:` + `StaticPool`) for fast unit tests. | M1 | R1 |
| 5 | Durable Usage Reporter | `DurableModelUsageReporter` implementing `core.model_runtime.usage.ModelUsageReporter` async protocol with atomic persistence. | M2 | R2 |
| 6 | Idempotency & Conflict Guard | Unique `operation_id` deduplication: identical payload returns no-op, conflicting payload raises `UsageEventConflictError`. | M2 | R2 |
| 7 | Fail-Fast Error Isolation | Reporter persistence failure raises `_ReporterFailure`, halting retry/fallback and preventing unpaid duplicate provider calls. | M2 | R2 |
| 8 | Resilient Attempt Billing | Persisting attempts on streaming interruption (`interrupted`), cancellation (`cancelled`), and provider errors with usage. | M2 | R5 |
| 9 | Unpriced Usage Handling | Catching `UnknownUsageCannotBePricedError` for `source="none"`, saving `usage_status="pending"`, never free zero tokens. | M2 | R5 |
| 10 | Web Lifespan Reporter Injection | Registering `configure_usage_reporter()` and `shutdown_usage_reporter()` in `server/web/app.py` lifespan. | M2 | R2 |
| 11 | Worker Lifespan Reporter Injection | Registering `configure_usage_reporter()` and `shutdown_usage_reporter()` in `server/worker/runtime.py` worker initialization. | M2 | R2 |
| 12 | Call Admission & Bucket Limits | `QuotaService.admit_turn` checking concurrency locks (`nlp_quota_concurrency_locks`) and daily/weekly bucket limits. | M3 | R3 |
| 13 | Conservative Token Estimation | Integration with `factory.estimate_input_tokens()`; applying conservative token floor when estimate is `None`. | M3 | R3 |
| 14 | Dynamic Additional Reservation | `QuotaService.reserve_additional` with row-level locks and `entry_type='reserve_increment'` ledger entries for expensive capabilities. | M3 | R3 |
| 15 | Settlement & Reservation Release | `settle_usage_in_transaction` on attempt report and `finish_turn` releasing unconsumed reservation (`entry_type='release'`). | M3 | R3 |
| 16 | Error Taxonomy Distinction | Strict separation between `upstream_provider_quota_exhausted` (infrastructure) and user quota rejections (`quota_daily_exhausted`). | M3 | R5 |
| 17 | Task Contract Attribution Extension | Adding `reservation_id` and `request_id` to `TurnTask` in `gateway/dispatch.py` and `gateway/contracts.py`. | M4 | R4 |
| 18 | Redis Transport Serialization | Updating `TurnTaskCodec.dumps()` and `loads()` with backward compatibility for `reservation_id` and `request_id`. | M4 | R4 |
| 19 | Engine Execution Attribution Binding | Wrapping `_run_engine()` in `gateway/turn_execution.py` with `with bind_usage_attribution(attribution):`. | M4 | R4 |
| 20 | Worker Subagent Attribution Inheritance | Propagating parent `reservation_id` and setting `purpose="worker"`, `worker_id=worker_id` in `server/tools/worker_tool.py`. | M4 | R4 |
| 21 | Full E2E Test Pass | Passing 100% of Tiers 1-4 tests in the E2E test suite covering all 16 acceptance criteria and R1-R5. | M5 | AC |
| 22 | Adversarial Hardening & Lint Clean | Tier 5 adversarial tests, edge cases, invariants, and clean `ruff check` verification. | M5 | AC |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Quota Schema, Models & Pricing Engine | Features 1, 2, 3, 4 | None | DONE |
| M2 | Durable Reporter & Lifecycle Injection | Features 5, 6, 7, 8, 9, 10, 11 | M1 | DONE |
| M3 | Admission, Reservation & Settlement | Features 12, 13, 14, 15, 16 | M1, M2 | DONE |
| M4 | Cross-Process Attribution & Task Boundaries | Features 17, 18, 19, 20 | M2, M3 | DONE |
| M5 | E2E Test Suite Pass & Adversarial Hardening | Features 21, 22 | M1, M2, M3, M4, E2E Track | DONE |

## Dual Track: E2E Testing Track
| Track | Scope | Outputs | Dependencies | Status |
|---|---|---|---|---|
| E2E Testing Track | Design & implement opaque-box test suite (Tiers 1-4) derived from user requirements and acceptance criteria | `TEST_INFRA.md`, `tests/e2e/test_quota_e2e_suite.py`, `TEST_READY.md` | None | DONE |

## Interface Contracts
### `core.model_runtime.usage` ↔ `server.quota.reporting`
- Protocol:
  ```python
  class ModelUsageReporter(Protocol):
      async def report(self, invocation: ModelInvocation, usage: CanonicalTokenUsage, outcome: InvocationOutcome) -> None: ...
  ```
- Implementation: `DurableModelUsageReporter(engine, quota_service=None)`
- Invariant: Every unique attempt has a distinct UUIDv4 `operation_id`. Replay with identical payload is a no-op; mismatch raises `UsageEventConflictError`.

### `server.quota.service` ↔ `gateway.turn_execution`
- Functions:
  ```python
  admit_turn(user_id: str, workspace_id: str | None, model_profile: str, estimated_input_tokens: int | None) -> ReservationResult
  reserve_additional(reservation_id: str, additional_micro: int, idempotency_key: str) -> None
  finish_turn(reservation_id: str, status: str) -> None
  ```
- Errors: `QuotaRejectedError` with reason `quota_daily_exhausted`, `quota_weekly_exhausted`, or `concurrency_limit`.

### `gateway.redis_transport` ↔ `server.worker.runtime`
- Serialization: `TurnTaskCodec.dumps(task)` includes `reservation_id` and `request_id`.
- Execution: Worker executes task within `with bind_usage_attribution(attribution):`.

## Code Layout
- `server/quota/`: Core quota models, pricing, reporting, and service implementation.
- `gateway/`: Dispatch contracts, Redis serialization, and turn execution attribution binding.
- `server/web/app.py`: Web lifespan reporter configuration.
- `server/worker/runtime.py`: Worker startup reporter configuration.
- `server/tools/worker_tool.py`: Subagent worker attribution propagation.
- `tests/`: Unit and integration test suites.
- `tests/e2e/`: Opaque-box E2E test suite.
