import { controlPlaneUrl, groupEventsByTrace, groupTracesIntoChains, monitorUrl, resetMonitorData, telemetryFrame } from "./monitor-helpers";
import type { TelemetryEvent, Trace } from "./api";

const trace = (traceId: string): Trace => ({
  trace_id: traceId, request_id: `request-${traceId}`, session_id: "session", turn_id: `turn-${traceId}`,
  workspace_id: "workspace", user_id: "user", channel: "web", source: "user", started_at: "2026-07-20T10:00:00Z",
  status: "ok", input_tokens: 0, output_tokens: 0, cached_tokens: 0, cache_miss_tokens: 0, reasoning_tokens: 0, total_tokens: 0, attributes: {},
});

const event = (eventId: string, timestamp: string, traceId?: string): TelemetryEvent => ({
  event_id: eventId, timestamp, level: "info", name: "agent.run", trace_id: traceId, payload: {},
});

describe("MonitorApp helpers", () => {
  it("returns to the control plane on the same remote host and the paired environment port", () => {
    expect(controlPlaneUrl({ protocol: "https:", hostname: "nlp.example.test", port: "18766" } as Location))
      .toBe("https://nlp.example.test:18765/developer");
  });

  it("opens the monitor on the paired environment port from the control plane", () => {
    expect(monitorUrl({ protocol: "https:", hostname: "nlp.example.test", port: "18765" } as Location))
      .toBe("https://nlp.example.test:18766");
  });

  it("ignores malformed telemetry frames", () => {
    expect(telemetryFrame("not-json")).toBeNull();
    expect(telemetryFrame('{"type":"other"}')).toBeNull();
    expect(telemetryFrame('{"type":"telemetry.event","payload":{}}')).toBeNull();
    expect(telemetryFrame('{"type":"telemetry.event","payload":[]}')).toBeNull();
    expect(telemetryFrame('{"type":"telemetry.event","payload":{"event_id":"e","timestamp":"bad","level":"info","name":"x","payload":{}}}')).toBeNull();
  });

  it("resets persisted monitor data before reloading the empty dashboard", async () => {
    const reset = vi.fn().mockResolvedValue({});
    const reload = vi.fn().mockResolvedValue({});

    await resetMonitorData(reset, reload);

    expect(reset).toHaveBeenCalledOnce();
    expect(reload).toHaveBeenCalledOnce();
    expect(reset.mock.invocationCallOrder[0]).toBeLessThan(reload.mock.invocationCallOrder[0]);
  });

  it("groups the live event stream by trace and isolates unlinked system events", () => {
    const groups = groupEventsByTrace(
      [
        event("unlinked", "2026-07-20T10:03:00Z"),
        event("first", "2026-07-20T10:02:00Z", "trace-a"),
        event("second", "2026-07-20T10:01:00Z", "trace-a"),
        event("third", "2026-07-20T10:00:00Z", "trace-b"),
      ],
      [trace("trace-a"), trace("trace-b")],
    );

    expect(groups.map((group) => group.traceId)).toEqual([undefined, "trace-a", "trace-b"]);
    expect(groups[1].trace?.turn_id).toBe("turn-trace-a");
    expect(groups[1].events.map((item) => item.event_id)).toEqual(["first", "second"]);
    expect(groups[0].events.map((item) => item.event_id)).toEqual(["unlinked"]);
  });

  it("groups primary and worker-resume traces from one turn into one chain", () => {
    const chains = groupTracesIntoChains([
      { ...trace("worker-resume"), session_id: "session-a", turn_id: "turn-a", source: "worker_resume", started_at: "2026-07-20T10:02:00Z" },
      { ...trace("primary"), session_id: "session-a", turn_id: "turn-a", source: "user", started_at: "2026-07-20T10:00:00Z" },
      { ...trace("other"), session_id: "session-a", turn_id: "turn-b", source: "user", started_at: "2026-07-20T10:01:00Z" },
    ]);

    expect(chains).toHaveLength(2);
    expect(chains[0].turnId).toBe("turn-a");
    expect(chains[0].traces.map((item) => item.trace_id)).toEqual(["primary", "worker-resume"]);
  });

  it("puts isolated evaluation-case sessions into their shared evaluation-run drawer", () => {
    const chains = groupTracesIntoChains([
      { ...trace("case-two"), session_id: "session-two", turn_id: "turn-two", attributes: { evaluation_run_id: "batch-1", evaluation_suite_id: "suite", evaluation_case_id: "case-two" }, started_at: "2026-07-20T10:02:00Z" },
      { ...trace("case-one"), session_id: "session-one", turn_id: "turn-one", attributes: { evaluation_run_id: "batch-1", evaluation_suite_id: "suite", evaluation_case_id: "case-one" }, started_at: "2026-07-20T10:01:00Z" },
      { ...trace("chat"), session_id: "chat-session", turn_id: "chat-turn" },
    ]);

    expect(chains).toHaveLength(2);
    expect(chains[0]).toMatchObject({ evaluationRunId: "batch-1", evaluationSuiteId: "suite" });
    expect(chains[0].traces.map((item) => item.attributes.evaluation_case_id)).toEqual(["case-one", "case-two"]);
    expect(chains[1].turnId).toBe("chat-turn");
  });
});
