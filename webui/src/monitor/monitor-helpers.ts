import type { TelemetryEvent, Trace } from "./api";

export type BrowserLocation = Pick<Location, "protocol" | "hostname" | "port">;
export type MonitorPage = "overview" | "traces" | "sessions" | "errors" | "events" | "storage" | "sandbox";

// Keep navigation and telemetry helpers outside the React entry module so Fast Refresh sees a component-only boundary.
export function monitorPageFromLocation(current: Pick<Location, "search"> = location): MonitorPage {
  const candidate = new URLSearchParams(current.search).get("page");
  return ["overview", "traces", "sessions", "errors", "events", "storage", "sandbox"].includes(candidate ?? "")
    ? candidate as MonitorPage
    : "overview";
}

function pairedPort(current: BrowserLocation, service: "web" | "monitor"): number {
  const currentPort = Number.parseInt(current.port, 10);
  const sourcePort = Number.isFinite(currentPort) && currentPort > 0
    ? currentPort
    : service === "monitor" ? 8766 : 8765;
  return service === "monitor" ? sourcePort - 1 : sourcePort + 1;
}

function originForPort(current: BrowserLocation, port: number): string {
  const hostname = current.hostname.includes(":") ? `[${current.hostname}]` : current.hostname;
  const origin = new URL(`${current.protocol}//${hostname}`);
  origin.port = String(port);
  return origin.origin;
}

export function controlPlaneUrl(current: BrowserLocation = location): string {
  return `${originForPort(current, pairedPort(current, "monitor"))}/developer`;
}

export function monitorUrl(current: BrowserLocation = location): string {
  return originForPort(current, pairedPort(current, "web"));
}

export async function resetMonitorData(reset: () => Promise<unknown>, reload: () => Promise<unknown>) {
  await reset();
  await reload();
}

export function telemetryFrame(data: unknown): { type: string; payload: TelemetryEvent } | null {
  try {
    const frame = JSON.parse(String(data)) as { type?: unknown; payload?: unknown };
    const payload = frame.payload as Partial<TelemetryEvent> | undefined;
    return frame.type === "telemetry.event"
      && typeof payload === "object" && payload !== null && !Array.isArray(payload)
      && typeof payload.event_id === "string" && payload.event_id.length > 0
      && typeof payload.timestamp === "string" && Number.isFinite(Date.parse(payload.timestamp))
      && typeof payload.level === "string" && payload.level.length > 0
      && typeof payload.name === "string" && payload.name.length > 0
      && typeof payload.payload === "object" && payload.payload !== null && !Array.isArray(payload.payload)
      ? frame as { type: string; payload: TelemetryEvent }
      : null;
  } catch {
    return null;
  }
}

export interface EventGroup {
  trace: Trace | undefined;
  traceId: string | undefined;
  events: TelemetryEvent[];
}

export interface TraceChain {
  key: string;
  sessionId: string;
  turnId: string;
  evaluationRunId?: string;
  evaluationSuiteId?: string;
  traces: Trace[];
}

/** A user turn may produce a primary trace plus worker-resume traces. Evaluation
 * cases intentionally have isolated sessions, so their shared evaluation run is
 * the parent chain shown in the Monitor. */
export function groupTracesIntoChains(traces: Trace[]): TraceChain[] {
  const chains = new Map<string, TraceChain>();
  for (const trace of traces) {
    const evaluationRunId = typeof trace.attributes.evaluation_run_id === "string" ? trace.attributes.evaluation_run_id : undefined;
    const evaluationSuiteId = typeof trace.attributes.evaluation_suite_id === "string" ? trace.attributes.evaluation_suite_id : undefined;
    const key = evaluationRunId ? `evaluation\u0000${evaluationRunId}` : `${trace.session_id}\u0000${trace.turn_id || trace.trace_id}`;
    const chain = chains.get(key) ?? {
      key,
      sessionId: evaluationRunId ? "evaluation" : trace.session_id,
      turnId: evaluationRunId ?? trace.turn_id,
      evaluationRunId,
      evaluationSuiteId,
      traces: [],
    };
    chain.traces.push(trace);
    chains.set(key, chain);
  }
  return [...chains.values()]
    .map((chain) => ({ ...chain, traces: [...chain.traces].sort((left, right) => Date.parse(left.started_at) - Date.parse(right.started_at)) }))
    .sort((left, right) => Date.parse(right.traces.at(-1)?.started_at ?? "") - Date.parse(left.traces.at(-1)?.started_at ?? ""));
}

/** Keeps events in the same run they originated from instead of one global feed. */
export function groupEventsByTrace(events: TelemetryEvent[], traces: Trace[]): EventGroup[] {
  const traceById = new Map(traces.map((trace) => [trace.trace_id, trace]));
  const groups = new Map<string, EventGroup>();
  for (const event of events) {
    const key = event.trace_id ?? "__unlinked__";
    const group = groups.get(key) ?? {
      trace: event.trace_id ? traceById.get(event.trace_id) : undefined,
      traceId: event.trace_id,
      events: [],
    };
    group.events.push(event);
    groups.set(key, group);
  }
  return [...groups.values()].sort((left, right) =>
    Date.parse(right.events[0]?.timestamp ?? "") - Date.parse(left.events[0]?.timestamp ?? ""),
  );
}
