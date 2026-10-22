# Chat database write pressure, 2026-10-21

## Real TEST result

Deployed revision: `3bedf5181a2bf8126ba31d4178d13f935f75ed54`.

The original two Workers at concurrency four completed all 41 simultaneous
short questions, but first visible reply P95 was 57.281 seconds; 15/41 were
within the user's 20 second target.

A temporary two-by-eight trial passed its isolated Docker recreate/restore
rehearsal and the initial eight-user stage. Its 41-user stage confirmed only
27 completions, ten terminal failures and four WebSocket protocol failures.
There were 31 visible-reply samples, of which 12 were within 20 seconds.
Two of 45 existing-session observations failed. This profile is rejected.

The supervisor returned the actual TEST Workers to their saved two-by-four
specifications. The subsequent read-only audit confirmed readiness, zero active
turns and zero queue pending/lag. Production was not changed.

The trial's successful resource samples showed minimum available memory
3623.18 MiB, maximum sampled CPU busy 79.1%, and maximum MySQL connections 46.
These are bounded samples; the failing health sample is not included in those
extrema. They do not establish an unlimited resource or upstream-provider budget.

## Failure evidence

In the 12:44:00–12:47:30 UTC TEST window, durable rows contained 35 completed
turns (including the initial eight) and ten failures. Error messages classified
as three runtime-reset named-lock timeouts, five connection-pool timeouts, and
two other errors; one failed kind was `usage_preparation_timeout`.

Retained structured logs also identified the reset-fence telemetry writer,
connection acquisition and checkpoint paths. A Redis read timeout was present
and its consume loop already retries such failures. There was no MySQL server
connection-limit error. Absence of a model-429 log signature does not prove
that the upstream provider has no concurrency/RPM/TPM limits.

Every gateway event write and telemetry batch currently takes the same MySQL
reset-fence named lock. Ordinary model text chunks previously each incurred a
durable gateway event transaction. Telemetry prepared and inserted each row
separately while holding that fence. Increasing the number of in-flight Turns
increases contention and cannot by itself establish the 20 second target.

## Local changes, not deployed

1. `DeltaBatcher` immediately persists each channel's first visible text.
   Subsequent adjacent string fragments are combined up to 128 characters or
   the next fragment after 100 ms. Channel changes, tool/model boundaries and
   normal stream completion flush pending text. The time check runs on arrivals;
   it is not an independent wall-clock timer during an upstream pause.
   Structured/nonstandard payloads pass through. Cancellation or stream failure
   does not create a delayed writer that could outlive the Turn's fence.
2. Telemetry serialization/redaction occurs before acquiring the reset fence.
   The existing single atomic transaction and reset protection remain. Upserts
   contain at most sixteen rows and target at most 256 KiB per statement, with
   an individually oversized row kept separate as before. Stable record IDs,
   last-update semantics and redaction remain. A hundred small envelopes now
   compile to seven upsert statements rather than a hundred.

The reset lock is not removed or made optional. Worker concurrency, pool sizes,
quota/pricing rules, accounts and deployment files are not changed by this patch.

### Incremental streamed-event storage settlement

Further inspection found that every `append_event` called storage finalization
with a full reconciliation. That reads twelve core-history aggregates and one
file aggregate, plus uploaded-image filesystem bytes, while holding the shared
runtime fence and storage-pool row lock.

An event is an immutable insertion, so it now measures the inserted MySQL JSON
payload's `OCTET_LENGTH(CAST(payload_json AS CHAR))` and settles that exact delta
in the same transaction. Initial ledger reconciliation, role/override limits,
global capacity validation, the reservation lifecycle and stale-worker fences
remain. JSON preparation also runs before acquiring the runtime fence. This
replaces thirteen history aggregates per initialized event with one lookup of
the inserted row; it does not claim an end-to-end server speedup by itself.

Real-MySQL regression coverage checks Unicode/nested JSON byte accounting against
full reconciliation, atomic rollback after deliberately underestimated final
usage, and rejected writes from an old worker. These tests run only in isolated
CI, not against a user's configured business database.

## Verification and remaining gates

28 local regressions passed: streaming content/order/first text, cancellation,
bounded SQL batches/redaction, existing engine behavior, repository queries and
reset-fence contracts. The local runner uses an inert process-global telemetry
sink; tests that inspect telemetry install their own isolated repository. SQL
tests compile MySQL statements against captured connections. Ruff and diff checks
passed. These are not real MySQL, runsc or production-load results.

The changes are consolidated on one branch. The user's latest instruction is to
continue optimizing but defer PR submission. The database commit `9cbe2f7` passed CI run
`39613088460`: 2043 backend tests passed, 159 skipped, with frontend checks and
Docker build passing. The new isolated real-MySQL test verifies bulk upsert
last-value semantics, whole-batch rollback and reset-lock release. It checks the
deliberate CHECK-constraint violation by MySQL code 3819 rather than assuming a
driver-specific exception subclass. No TEST or production deployment has been
performed from this branch. The combined head still requires its own CI and
deployed load acceptance. After a consolidated review and TEST
release, first recheck the original two-by-four configuration; then compare higher
concurrency with an explicit per-process pool/total connection budget in the same
bounded staged run. No higher-capacity configuration or 20 second result is accepted
yet. Do not repeat the old trial marker to launch more load.

## File-send modal stacking fix in the same PR

The file-send overlay was nested inside `.tool-dock`, whose desktop stacking
context has z-index 34. The learning-topic menu has z-index 45, so it could cover
the confirmation button despite the nested overlay's z-index 110.

Render the existing overlay through a React portal into `document.body`. Its
unchanged fixed-position overlay now participates in the page stacking context
at z-index 110. The dialog remains centered; recipient lookup, preflight,
confirmation eligibility and file-transfer requests are unchanged.

The existing FilesPanel and LearningContextBar suites pass (23 tests), as do
component ESLint and frontend TypeScript checks. This is local regression
evidence, not a deployed-browser acceptance result. After TEST deployment, verify
that clicking confirmation reaches the send action and does not open the
background learning-topic menu.

## Selected TEST release and server evidence

PR #268 selects two TEST Workers with sixteen slots each. The TEST-only release
overlay explicitly overrides stale server environment/Compose values:

| Setting per Worker | Selected value |
| --- | --- |
| Worker concurrency | 16 |
| Async pool / overflow per engine | 8 / 2 |
| Shared synchronous pool / overflow | 16 / 2 |
| Pool timeout | 5 seconds |

The generic Compose defaults and production release behavior are unchanged.
New TEST releases use two replicas even if the preceding deployment has one;
the existing guard still refuses ambiguous, stopped or foreign instances before
recreation. The selected profile is included in immutable release identity.
Resolved Compose and deployed container checks reject the wrong count, pools
or concurrency. A read-only Redis check waits for TTL-backed heartbeats from
both actual deployed container hostnames reporting sixteen slots each. Container
state alone is insufficient to pass release verification. Earlier prepared
release schemas remain readable for recovery.

The release retains the original four-file backend optimization from 43b58b7
and the file-send portal fix. A configuration validation fix permits higher-slot
experiments, but does not select a higher deployment profile. Two Web admission
experiments were tested and reverted on the server, and are excluded from this
release. Their history remains in earlier commits and private aggregate reports:

| 41-user short-chat comparison | Completed | Within 20 seconds | First-text P95 | Maximum |
| --- | --- | --- | --- | --- |
| Selected four-file Web + Worker optimization, 2x16 | 41/41 | 25/41 | 25.016 s | 25.906 s |
| Empty-expiry lookup experiment (not retained) | 41/41 | 32/41 | 26.937 s | 27.187 s |
| Lookup + single-query storage experiment (not retained) | 41/41 | 32/41 | 26.250 s | 27.390 s |

Existing-session observations and sampled resource/error checks passed for the
selected run. These are individual bounded real-server short-chat measurements,
not guaranteed response times or sustained/tool-heavy acceptance. The original
target of most first replies within twenty seconds has **not** passed the
chosen strict gate. A post-merge TEST run must confirm the final deployed image,
actual 2x16 heartbeats and end-to-end visible response times. Do not label the
release accepted solely because deployment or local/CI tests succeed.


## Worker configuration validation before a larger slot trial

The first runtime-only 2x24 attempt stopped while starting Workers, before any
new chat task was sent. Both Workers were restored to the verified 2x16
configuration. This is not a 2x24 throughput result.

Inspection found a deterministic startup blocker: RedisTransportConfig rejects
worker_concurrency above sixteen. The preceding operational rehearsal only
started sleeping Python probes, so it did not exercise that application
configuration validation. That omission is corrected by requiring the actual
candidate image to instantiate the same RedisTransportConfig with the proposed
value before either live Worker is replaced.

The configuration ceiling is now thirty-two, and the default remains four.
This permits the bounded twenty-four-slot comparison; it does not select
twenty-four or thirty-two for deployment, resize database pools, or establish
that this host can sustain them. Bounds/default regressions and the existing
worker occupancy test cover twenty-four and thirty-two, including refusing
extra work until a slot is released. Resource, queue and existing-session
acceptance still need the real TEST comparison.
