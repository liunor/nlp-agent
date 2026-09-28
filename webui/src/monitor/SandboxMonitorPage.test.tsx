import { fireEvent, render, screen } from "@testing-library/react";

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
  type SandboxRuntime,
} from "./SandboxMonitorPage";

const now = Date.parse("2026-08-28T03:00:00.000Z");
const sampledNow = now / 1000;

const overview: SandboxOverview = {
  runtime_states: { creating: 1, ready_unbound: 4, claiming: 0, assigned: 3, draining: 1, failed: 0 },
  capacity: { ready: 4, creating: 1, target: 5, deficit: 0, adaptive_target: 5, arrival_rate_per_min: 1.2, online_count: 7, unassigned_count: 2, role_demand: { developer: 1, student: 1 } },
  execution_latency: { sample_count: 42, p50_ms: 320, p95_ms: 860, p99_ms: 1200 },
  active_executions: 3,
  recent_failures: 1,
  alerts: [],
  capacity_history: [
    { timestamp: sampledNow - 120, ready: 2, creating: 2, target: 5, adaptive_target: 3, deficit: 1 },
    { timestamp: sampledNow - 60, ready: 3, creating: 1, target: 5, adaptive_target: 4, deficit: 0 },
    { timestamp: sampledNow, ready: 4, creating: 1, target: 5, adaptive_target: 5, deficit: 0 },
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

  it("replaces a repeated server sample instead of inventing a new timestamp", () => {
    const current: SandboxCapacitySample[] = [
      { timestamp: sampledNow - 4, ready: 2, creating: 1, target: 5, deficit: 2 },
      { timestamp: sampledNow - 2, ready: 3, creating: 1, target: 5, deficit: 1 },
    ];
    const incoming: SandboxCapacitySample[] = [
      { timestamp: sampledNow - 2, ready: 4, creating: 0, target: 5, deficit: 0 },
    ];

    const visible = mergeSandboxCapacitySamples(current, incoming);

    expect(visible).toHaveLength(2);
    expect(visible.at(-1)).toMatchObject({ timestamp: sampledNow - 2, ready: 4, creating: 0 });
    expect(visible.map((sample) => sample.timestamp)).toEqual([
      sampledNow - 4,
      sampledNow - 2,
    ]);
  });

  it("refreshes the sandbox signal without turning the chart into a ticker", () => {
    expect(SANDBOX_REFRESH_INTERVAL_MS).toBe(5_000);
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
    expect(screen.getByText(/在线 7/)).toBeVisible();
    expect(screen.getByText(/开发者 1/)).toBeVisible();
    expect(screen.getByText("近期故障")).toBeVisible();
    expect(screen.getAllByText("自适应目标").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("运行日志")).toBeVisible();
    expect(screen.getByText("运行时异常")).toBeVisible();
  });

  it("exposes runtime detail, execution replay, preload matrix, and manual prewarm", () => {
    const runtime: SandboxRuntime = {
      id: "runtime-1", state: "ready_unbound", node_id: null, runtime_kind: "docker",
      resource_profile_id: "python-base", external_runtime_id: "container-1", failure_reason: null,
      updated_at: new Date(now).toISOString(), image_digest: "sha256:test", generation: 2,
      environment_id: null, last_heartbeat_at: new Date(now).toISOString(),
    };
    const onPrewarm = vi.fn();
    render(<SandboxMonitorPage
      overview={{ ...overview, capacity: { ...overview.capacity, host_total: 2, host_total_max: 4, host_available: 2, host_budget_blocked_count: 1 } }}
      logs={[]}
      runtimes={[runtime]}
      executions={[{ id: "execution-1", owner_user_id: "user-1", environment_id: "env-1", runtime_instance_id: "runtime-1", status: "completed", generation: 2, started_at: new Date(now).toISOString(), completed_at: new Date(now).toISOString(), exit_reason: null }]}
      preloadMatrix={{ source: "ci", profiles: { "python-base": { status: "compatible", image_digest: "sha256:test" } } }}
      onPrewarm={onPrewarm}
      live loading={false} logLoading={false} onRefresh={() => undefined} onDrain={() => undefined}
    />);
    expect(screen.getByText("主机可用预算")).toBeVisible();
    expect(screen.getByText("预加载兼容矩阵")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "手动预热" }));
    fireEvent.click(screen.getByRole("button", { name: "提交预热" }));
    expect(onPrewarm).toHaveBeenCalledWith({ expected_sessions: 4, sessions_per_runtime: 1, ttl_seconds: 900 });
  });

  it("offers explicit capacity history windows without changing the page refresh loop", () => {
    const onWindowChange = vi.fn();
    render(
      <SandboxMonitorPage
        overview={overview}
        logs={[]}
        runtimes={[]}
        executions={[]}
        historyMinutes={30}
        onHistoryMinutesChange={onWindowChange}
        live
        loading={false}
        logLoading={false}
        onRefresh={() => undefined}
        onDrain={() => undefined}
      />,
    );

    const group = screen.getByRole("group", { name: "容量趋势时间范围" });
    expect(group).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "2 小时" }));
    expect(onWindowChange).toHaveBeenCalledWith(120);
  });

  it("keeps rendering the chart when serialized timestamps arrive as strings", () => {
    render(
      <SandboxMonitorPage
        overview={{ ...overview, capacity_history: [
          { ...overview.capacity_history[0], timestamp: String(overview.capacity_history[0].timestamp) } as unknown as SandboxCapacitySample,
          { ...overview.capacity_history[1], timestamp: String(overview.capacity_history[1].timestamp) } as unknown as SandboxCapacitySample,
        ] }}
        logs={[]}
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
  });

  it("shows the chart frame while the first capacity sample is warming up", () => {
    render(
      <SandboxMonitorPage
        overview={{ ...overview, capacity_history: [overview.capacity_history[0]] }}
        logs={[]}
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
    expect(screen.getByText("正在积累趋势样本")).toBeVisible();
  });

  it("anchors a readable tooltip to the focused capacity sample", () => {
    render(
      <SandboxMonitorPage
        overview={overview}
        logs={[]}
        runtimes={[]}
        executions={[]}
        live
        loading={false}
        logLoading={false}
        onRefresh={() => undefined}
        onDrain={() => undefined}
      />,
    );

    const sample = screen.getByRole("button", { name: /待命 4/ });
    fireEvent.focus(sample);
    expect(screen.getByRole("status")).toHaveTextContent("缺口 0");
    expect(screen.getByRole("status")).toHaveAttribute("data-placement", "below");
  });
});
