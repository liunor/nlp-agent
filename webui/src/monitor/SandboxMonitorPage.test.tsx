import { render, screen } from "@testing-library/react";

import {
  MAX_SANDBOX_LOGS,
  SANDBOX_REFRESH_INTERVAL_MS,
  SANDBOX_LOG_RETENTION_MS,
  SandboxMonitorPage,
  filterSandboxLogs,
  mergeSandboxCapacitySamples,
  mergeSandboxLogs,
  type SandboxCapacitySample,
  type SandboxLogEntry,
  type SandboxOverview,
} from "./SandboxMonitorPage";

const now = Date.parse("2026-08-28T03:00:00.000Z");
const sampledNow = now / 1000;

const overview: SandboxOverview = {
  runtime_states: { creating: 1, ready_unbound: 4, claiming: 0, assigned: 3, draining: 1, failed: 0 },
  capacity: { ready: 4, creating: 1, target: 5, deficit: 0, adaptive_target: 5, arrival_rate_per_min: 1.2 },
  execution_latency: { sample_count: 42, p50_ms: 320, p95_ms: 860, p99_ms: 1200 },
  active_executions: 3,
  recent_failures: 1,
  alerts: [],
  capacity_history: [
    { timestamp: sampledNow - 120, ready: 2, creating: 2, target: 5, deficit: 1 },
    { timestamp: sampledNow - 60, ready: 3, creating: 1, target: 5, deficit: 0 },
    { timestamp: sampledNow, ready: 4, creating: 1, target: 5, deficit: 0 },
  ],
  sampled_at: new Date(now).toISOString(),
};

const log = (id: string, overrides: Partial<SandboxLogEntry> = {}): SandboxLogEntry => ({
  id,
  timestamp: new Date(now).toISOString(),
  level: "info",
  event_type: "execution.completed",
  message: "执行完成",
  ...overrides,
});

describe("SandboxMonitorPage", () => {
  it("filters debug, heartbeat, and metrics noise before rendering the log stream", () => {
    const visible = filterSandboxLogs([
      log("debug", { level: "debug" }),
      log("heartbeat", { event_type: "sandbox.heartbeat" }),
      log("metrics", { event_type: "sandbox.metrics.sample" }),
      log("failure", { level: "error", event_type: "execution.failed", message: "运行时异常" }),
    ]);

    expect(visible.map((item) => item.id)).toEqual(["failure"]);
  });

  it("keeps a bounded hot log window and removes expired entries", () => {
    const expired = log("expired", { timestamp: new Date(now - SANDBOX_LOG_RETENTION_MS - 1).toISOString() });
    const incoming = Array.from({ length: MAX_SANDBOX_LOGS + 20 }, (_, index) =>
      log(`log-${index}`, { timestamp: new Date(now - index * 1000).toISOString() }),
    );

    const visible = mergeSandboxLogs([expired], incoming, now);

    expect(visible).toHaveLength(MAX_SANDBOX_LOGS);
    expect(visible.some((item) => item.id === "expired")).toBe(false);
    expect(new Set(visible.map((item) => item.id)).size).toBe(MAX_SANDBOX_LOGS);
  });

  it("does not retain log entries without a timestamp", () => {
    const visible = mergeSandboxLogs([log("untimestamped", { timestamp: null })], [], now);

    expect(visible).toEqual([]);
  });

  it("keeps a rolling capacity timeline when the server repeats its latest sample", () => {
    const current: SandboxCapacitySample[] = [
      { timestamp: sampledNow - 4, ready: 2, creating: 1, target: 5, deficit: 2 },
      { timestamp: sampledNow - 2, ready: 3, creating: 1, target: 5, deficit: 1 },
    ];
    const incoming: SandboxCapacitySample[] = [
      { timestamp: sampledNow - 2, ready: 4, creating: 0, target: 5, deficit: 0 },
    ];

    const visible = mergeSandboxCapacitySamples(current, incoming, now);

    expect(visible).toHaveLength(3);
    expect(visible.at(-1)).toMatchObject({ timestamp: sampledNow, ready: 4, creating: 0 });
    expect(visible.map((sample) => sample.timestamp)).toEqual([
      sampledNow - 4,
      sampledNow - 2,
      sampledNow,
    ]);
  });

  it("refreshes the sandbox signal at a realtime cadence", () => {
    expect(SANDBOX_REFRESH_INTERVAL_MS).toBe(2_000);
  });

  it("renders an accessible live capacity chart and concise operational cards", () => {
    render(
      <SandboxMonitorPage
        overview={overview}
        logs={[log("failure", { level: "error", event_type: "execution.failed", message: "运行时异常" })]}
        runtimes={[]}
        executions={[]}
        live
        loading={false}
        logLoading={false}
        onRefresh={() => undefined}
        onDrain={() => undefined}
      />,
    );

    expect(screen.getByRole("img", { name: "Sandbox 容量实时趋势" })).toBeVisible();
    const chart = screen.getByRole("img", { name: "Sandbox 容量实时趋势" });
    const expectedTime = new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    }).format(new Date(now));
    expect(chart.textContent).toContain(expectedTime);
    expect(chart.textContent).not.toContain("1970");
    expect(screen.getByText("运行中")).toBeVisible();
    expect(screen.getByText("近期故障")).toBeVisible();
    expect(screen.getByText("运行日志")).toBeVisible();
    expect(screen.getByText("运行时异常")).toBeVisible();
  });
});
