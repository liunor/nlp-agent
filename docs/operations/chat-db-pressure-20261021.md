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

## Verification and remaining gates

28 local regressions passed: streaming content/order/first text, cancellation,
bounded SQL batches/redaction, existing engine behavior, repository queries and
reset-fence contracts. The local runner uses an inert process-global telemetry
sink; tests that inspect telemetry install their own isolated repository. SQL
tests compile MySQL statements against captured connections. Ruff and diff checks
passed. These are not real MySQL, runsc or production-load results.

PR creation and deployment are deferred at the user's request. A branch push can
run CI without creating a PR or deploying TEST. A new CI-only MySQL test checks
bulk upsert last-value semantics, whole-batch rollback and reset-lock release on
an isolated temporary table. Real MySQL integration and deployed load acceptance
remain required. After a consolidated review and TEST
release, first recheck the original two-by-four configuration; then compare higher
concurrency with an explicit per-process pool/total connection budget in the same
bounded staged run. No higher-capacity configuration or 20 second result is accepted
yet. Do not repeat the old trial marker to launch more load.
