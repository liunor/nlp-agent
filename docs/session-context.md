# Session and Context Runtime

Every request must carry `configurable.thread_id`. `SessionContext` expands that
identity with `user_id`, `workspace_id`, `channel`, and `agent_id`; its encoded
storage key prevents filename collisions and path traversal. Coordinator and
each Worker therefore receive separate context state even inside one session.

The local runtime keeps three explicit layers:

- LangGraph checkpoint: executable Coordinator state.
- append-only JSONL transcript: audit and WebUI display history.
- `context-state.json`: per-agent collapse commits and compaction metadata.

`LocalSessionService` provides WebUI-friendly create/list/messages/delete
operations without reading or changing a process-global active session.
`CoordinatorRuntime.release_session()` cancels and releases only the selected
session.

## Five-layer model view

`ContextManager.prepare()` builds a temporary model-facing view under one
per-session lock. Automatic layers are monotonic within a call: after a layer
actually changes the view, that call stops. The hard trim is the only emergency
exception because the provider input limit must never be exceeded:

1. Large tool results are externalized by the tool persistence layer.
2. Re-fetchable old tool results are micro-compacted; a successful micro pass
   ends the current prepare call.
3. Coordinator-directed Snip uses the current call's `thread_id` and remains
   an explicit zero-LLM decision, not an overlapping automatic threshold.
4. Context Collapse stages below its commit pressure and commits on a later
   pressure pass; a successful projection ends the current prepare call.
5. Auto-Compact summarizes near the limit only when Collapse did not change
   the view. Its internal summary is watermark-aware, so replaying the same
   post-compaction view is a no-op. Legal hard trimming is the final overflow
   guard.

Synthetic summaries, Snip markers, and restoration messages are marked as
internal model context and are excluded from the public transcript. The
append-only transcript is not destructively rewritten by read-time projection.

## Token budget

Provider configuration declares `context_window_tokens` and
`output_reserve_tokens`. The actual model input limit subtracts output reserve,
safety margin, and bound tool-schema tokens. Automatic thresholds are derived
from that runtime budget; legacy constants in individual compression modules are
only compatibility values for direct callers and tests. Estimates include
message framing, tool calls, tool results, artifacts, JSON punctuation, and
conservative mixed CJK/Latin costs. Historical API `total_tokens` is never reused
as if it were the size of a transformed context view.
