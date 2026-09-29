# Developer control plane and observability monitor

The developer experience is intentionally split into two deployment planes.

## Port ownership

| Port | Process | Responsibility |
|---|---|---|
| `8765` | `python main.py serve` | Student WebUI, `/developer/*` control plane, HTTP/WS chat, Gateway lifecycle |
| `8766` | `python main.py monitor` | Read-mostly observability API, Trace/Event queries, telemetry WebSocket, monitor UI |
| `5173` | `npm run dev` | Student/control-plane Vite server during frontend development |
| `5174` | `npm run dev:monitor` | Monitor Vite server during frontend development |

The monitor process never creates a `BackendGateway`, LangGraph runtime, Worker,
or tool runtime. It reads only the MySQL database configured by that deployment's
`NLP_AGENT_DATABASE_URL`; test and production must use different database
instances and credentials. A slow or failed monitor therefore cannot block
student chat traffic or cross an environment boundary.

## Stage 5: same-origin developer workspace

Open `http://127.0.0.1:8765/developer` after building the primary WebUI. The
database-backed session must resolve to the `developer` role. The workspace provides:

- Gateway and runtime status;
- Agent/Worker limits and profiles;
- registered tools, risk, scope, timeout, retry and policy snapshots;
- model routes, fallbacks, thinking presets and provider readiness;
- MCP and custom-tool discovery state;
- Skills and local workspace data roots;
- explicit unavailable states for Apps, Cron, Browser and Voice integrations.

The control plane has a complete Tool / Skill / MCP loop:

- Tool policies can be edited and take effect for newly-built ToolSets.
- Workspace Skills can be created, edited and deleted. They are stored in
  `.data/skills/<name>/SKILL.md` and reload immediately; a workspace Skill with
  the same name overrides a project Skill.
- Worker Profiles can be edited and reload with the Skill catalog.
- MCP servers can be tested with an isolated catalog, then saved and hot
  reconnected into the live Tool Runtime.
- Custom Python Tool discovery settings can be saved, but intentionally require
  an explicit Runtime restart: dynamically unloading imported Python modules is
  unsafe.

The editable state is stored atomically in
`.data/developer/runtime-overrides.yaml`, then merged with the commented base
configuration in `configs/agent_config.yaml`. This means WebUI changes never
rewrite or discard comments in the base configuration. Secret, password,
API-key, authorization and access-token fields are removed or reduced to a
configured boolean before snapshots are returned. Writes require the database
`developer` capability, same-origin validation, and CSRF validation.

## Stage 6: isolated monitor

Build and start:

```powershell
cd webui
npm run build:monitor
cd ..
uv run python main.py monitor
```

Then open `http://127.0.0.1:8766` from the internal network or VPN. Monitor
reuses the control-plane account, password, and account database, but uses an
independent monitor session and browser cookie so the two apps cannot
invalidate each other's CSRF token. If there is no valid monitor session, it
presents its own login page, so you do not need to open the WebUI first. The
login still requires the `system:runtime:monitor` permission; with the
built-in roles this means the `developer` account. The monitor login does not
log out or replace an existing control-plane session.

The platform includes:

- request count, error rate, response-time and TTFT percentiles;
- input/output/reasoning/cache-hit/cache-miss Token usage;
- session aggregates and error grouping;
- complete Trace details with Coordinator/Worker/model/tool spans;
- redacted Trace/Event metadata for debugging (prompts, outputs and credentials are never persisted);
- live telemetry events over `/ws/observability`;
- telemetry queue/database health and explicit retention cleanup;
- automatic monthly cleanup of Trace/Span/Event rows older than 30 days;
- authorization audit records retained for 180 days by default and pruned by
  the same monthly maintenance task.

The monitor uses the same environment's control-plane MySQL schema and requires
its own same-origin WebSocket ticket. Interactive OpenAPI documentation is
disabled on the monitor surface, and the public readiness probe only exposes
process status. Cleanup mutations still require CSRF
protection and the `system:runtime:monitor` permission; no production monitor
credential or database endpoint is shared with test. Automatic retention is
configured under `monitor.retention` in `configs/agent_config.yaml` (or with
the `NLP_AGENT_MONITOR_RETENTION_*` environment overrides). It only removes
monitor-owned observability rows; the canonical `nlp_usage_events` billing
ledger is retained.

## Frontend development

Run the backend processes in two terminals:

```powershell
uv run python main.py serve
uv run python main.py monitor
```

Run the frontend processes in two more terminals:

```powershell
cd webui
npm run dev
npm run dev:monitor
```

The Vite servers proxy their own `/api`, `/health`, and `/ws` paths, so cookies
remain same-origin from the browser's perspective.
