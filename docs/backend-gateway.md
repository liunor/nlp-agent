# Backend Gateway Core

The Backend Gateway is the only owner of Coordinator, Worker, LangGraph,
tool, memory, telemetry, and local persistence lifecycles.

```text
Browser / CLI / future channels
             |
       FastAPI adapter
             |
      BackendGateway
       /     |      \
 Turn store Events  LangGraphAgentEngine
   MySQL   MySQL       |
                    Coordinator/Worker
```

## Hosting rules

- Construct one `BackendGateway` per OS process.
- Call `await gateway.start()` during host startup.
- Route every turn, injection, cancellation, approval, and session deletion
  through the Gateway.
- On shutdown, call `await gateway.begin_shutdown()` before closing network
  channels, then call `await gateway.close()`. The Gateway rejects new turns,
  gives active turns `shutdown_grace_s` to finish, cancels the remainder, closes
  the Agent engine and its session/memory/tool/telemetry resources, and finally
  flushes pending persistence and closes MySQL resources.
- Do not run several Uvicorn workers with embedded Gateway instances. A future
  FastAPI layer may scale independently only when it talks to the single
  Gateway over an internal transport.

## Durable streaming

Every Gateway event has a per-turn monotonic `sequence`. Clients reconnect with
their last sequence and call `replay_events()` before resuming `stream_events()`.
The MySQL event log is authoritative; bounded live queues are only a latency
optimization. Persisting an event means it is available for replay, not that a
browser received it. There is intentionally no misleading `delivered` or
process-local outbox state without a downstream client ACK.

Retention compacts nonessential frames from old terminal turns and enforces a
per-session event cap. Events belonging to accepted or running turns are never
pruned. A replay whose requested sequence has expired emits `stream.gap`; the
client must then reload the final turn/session transcript over HTTP. Configure
`event_retention_days`, `max_events_per_session`, and
`retention_cleanup_interval_s` under `gateway`.

## Security boundary

HTTP adapters must construct `AuthenticatedPrincipal` from authenticated server
credentials. User and workspace identifiers must never be trusted from request
JSON. Session, Turn, Memory, Worker control, and Trace access are checked against
that principal before data is returned or mutated.

The concrete FastAPI lifecycle, same-origin authentication, HTTP routes, and
WebSocket protocol are documented in [`web-api.md`](web-api.md).
