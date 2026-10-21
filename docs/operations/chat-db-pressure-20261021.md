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

## Admission: skip empty teaching-session cleanup writes

Every submitted message checks expiry of guided and exercise sessions, including
new ordinary conversations with no teaching rows. Those two no-op UPDATEs used
to acquire the shared runtime write/reset fence. A bounded nonlocking lookup now
returns immediately when no active row is older than the unchanged cutoff.

When a candidate exists, the read connection closes before acquiring a writer.
The original fenced transaction rechecks the same conversation, active status
and cutoff predicate. A concurrently refreshed, completed or deleted row is not
expired based on the earlier hint. This is opportunistic expiry cleanup; a row
committed after an empty lookup is reconsidered by a later cleanup call. The
cutoff, minimum idle period, final status and changed-row count are preserved.
The two table names are an internal allowlist, and user values remain bound.

Seven regressions cover the no-write path, releasing the reader before entering
the writer, a refreshed candidate producing zero changes, changed-row counts,
minimum cutoff and the table allowlist. The local repository suite passes 21
tests. These are correctness checks; deployed response-time improvement still
requires a bounded comparison.

### Latest server evidence before this admission change

The existing four-file optimization was tested with two Workers at sixteen
slots each, then also applied to TEST Web using the cached candidate image.
All 41 short-chat tasks completed in both runs. Worker-only first-text P95 was
26.390 seconds (29/41 within twenty seconds); optimized Web plus Workers measured
25.016 seconds (25/41 within twenty seconds). Existing-session checks had no
failures, and sampled resource/error checks passed. The twenty-second target
has **not** passed. Tail P95 and the within-twenty-second count must both be
reported; a smaller P95 alone does not establish an overall improvement.

These runtime candidates have not been consolidated into a release, and their
parameters are not saved in deployment configuration. The user continues to
defer PR creation. The next comparison should keep Worker settings unchanged,
apply only the new admission lookup to TEST Web, and retain a candidate only
when its functional/resource checks pass and its twenty-second count and P95
are both no worse, with at least one improving. This does not change accounts,
quota/pricing rules, sandbox behavior or production services.


## Batched synchronous full storage measurement

The bbedb75 Web admission trial completed all 41 chats and improved the count
within twenty seconds from 25 to 32. Its first-text P95 rose from 25.016 to
26.937 seconds, so the strict comparison reverted this Web candidate. Workers
remain at 2x16 with the original four-file optimization; the twenty-second
target is still unmet. These are single short-chat comparisons, not proof of
sustained throughput.

Full synchronous storage reconciliation still sends twelve core-history sums
and one file sum as thirteen sequential database queries inside the existing
runtime/storage locks. It now sends the same fixed, owner-filtered expressions
as scalar subqueries in one SELECT, retaining the MySQL JSON casts, NULL
definitions, file status filters and separate uploaded-image byte scan. No
usage source, initial reconciliation, reservation, final quota validation,
transaction rollback or reset fence is removed. This reduces round trips; it
does not reduce the amount of history scanned or establish a server speedup.

Regression checks compare all thirteen populated sources against the frozen
old implementation in isolated CI MySQL, including Unicode/JSON NULL/binary
values, two-owner isolation, empty data, trashed/deleted files, uploaded images
and a deliberately underestimated write that must roll back on final quota
validation. One database statement per measurement is checked explicitly.
Async reconciliation is unchanged. Runtime candidate validation remains separate
from these correctness checks. No PR is submitted.
