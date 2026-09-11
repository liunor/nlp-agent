import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const { monitorApi } = vi.hoisted(() => ({
  monitorApi: {
    login: vi.fn().mockResolvedValue({ csrf_token: "test-csrf" }),
    createWsTicket: vi.fn().mockResolvedValue({ ticket: "test-ticket", expires_in: 60 }),
    overview: vi.fn().mockResolvedValue({ requests: 0, errors: 0, error_rate: 0, period_days: 30, latency_ms: { p50: 0, p90: 0, p95: 0, p99: 0 }, ttft_ms: { p50: 0, p90: 0, p95: 0, p99: 0 }, tokens: {} }),
    dependencies: vi.fn().mockResolvedValue({ scope: "system", period_days: 30, from: "2026-09-04T08:00:00Z", to: "2026-09-04T10:00:00Z", summary: { requests: 0, component_calls: 0, errors: 0, component_errors: 0, error_rate: 0, active_users: 0, active_workspaces: 0, latency_ms: { p50: 0, p90: 0, p95: 0, p99: 0 }, ttft_ms: { p50: 0, p90: 0, p95: 0, p99: 0 }, total_tokens: 0 }, trend: [], components: [], providers: [], models: [], anomalies: [] }),
    traces: vi.fn().mockResolvedValue({ items: [] }), usage: vi.fn().mockResolvedValue({ items: [] }),
    traceGroups: vi.fn().mockResolvedValue({ items: [], total: 0, offset: 0, limit: 24, has_more: false }),
    traceGroup: vi.fn().mockResolvedValue({ chain: null, traces: [], spans: [], events: [] }),
    systemUsage: vi.fn().mockResolvedValue({ scope: "system", events: 0, priced_events: 0, unpriced_events: 0, credits_complete: true, credit_status: "complete", credits_micro: 0, priced_credits_micro: 0, tokens: {}, breakdown: [], users: [], workspaces: [], providers: [], purposes: [], models: [] }),
    systemUsageTrend: vi.fn().mockResolvedValue({ scope: "system", period_days: 1, from: "2026-09-04T08:00:00Z", to: "2026-09-04T10:00:00Z", granularity: "five_minute", events: 0, priced_events: 0, unpriced_events: 0, credits_complete: true, credit_status: "complete", credits_micro: 0, priced_credits_micro: 0, tokens: {}, breakdown: [], users: [], workspaces: [], providers: [], purposes: [], models: [] }),
    systemUsageUsers: vi.fn().mockResolvedValue({ items: [], total: 0, offset: 0, limit: 12, has_more: false }),
    errors: vi.fn().mockResolvedValue({ items: [] }),
    events: vi.fn().mockResolvedValue({ items: [] }), storage: vi.fn().mockResolvedValue({}),
    authorizationAudit: vi.fn().mockResolvedValue({ items: [], total: 0, offset: 0, limit: 50, has_more: false }),
    authorizationAuditStats: vi.fn().mockResolvedValue({ period_days: 30, since: "2026-08-01T00:00:00", total: 0, by_decision: {}, top_reasons: [] }),
  },
}));

const { authenticate } = vi.hoisted(() => ({ authenticate: vi.fn().mockResolvedValue({
  user_id: "developer",
  roles: ["developer"],
  permissions: ["system:runtime:monitor", "system:runtime:reset"],
  csrf_token: "test-csrf",
  expires_at: 1_800_000_000,
}) }));

vi.mock("./api", () => ({ authenticate, monitorApi }));

import { MonitorApp } from "./MonitorApp";

describe("MonitorApp navigation", () => {
  beforeEach(() => {
    authenticate.mockReset().mockResolvedValue({
      user_id: "developer",
      roles: ["developer"],
      permissions: ["system:runtime:monitor", "system:runtime:reset"],
      csrf_token: "test-csrf",
      expires_at: 1_800_000_000,
    });
    monitorApi.login.mockReset().mockResolvedValue({ csrf_token: "test-csrf" });
    monitorApi.createWsTicket.mockReset().mockResolvedValue({ ticket: "test-ticket", expires_in: 60 });
    monitorApi.events.mockReset().mockResolvedValue({ items: [] });
    monitorApi.systemUsage.mockReset().mockResolvedValue({ scope: "system", events: 0, priced_events: 0, unpriced_events: 0, credits_complete: true, credit_status: "complete", credits_micro: 0, priced_credits_micro: 0, tokens: {}, breakdown: [], users: [], workspaces: [], providers: [], purposes: [], models: [] });
    monitorApi.systemUsageTrend.mockReset().mockResolvedValue({ scope: "system", period_days: 1, from: "2026-09-04T08:00:00Z", to: "2026-09-04T10:00:00Z", granularity: "five_minute", events: 0, priced_events: 0, unpriced_events: 0, credits_complete: true, credit_status: "complete", credits_micro: 0, priced_credits_micro: 0, tokens: {}, breakdown: [], users: [], workspaces: [], providers: [], purposes: [], models: [] });
    monitorApi.dependencies.mockReset().mockResolvedValue({ scope: "system", period_days: 30, from: "2026-09-04T08:00:00Z", to: "2026-09-04T10:00:00Z", summary: { requests: 0, component_calls: 0, errors: 0, component_errors: 0, error_rate: 0, active_users: 0, active_workspaces: 0, latency_ms: { p50: 0, p90: 0, p95: 0, p99: 0 }, ttft_ms: { p50: 0, p90: 0, p95: 0, p99: 0 }, total_tokens: 0 }, trend: [], components: [], providers: [], models: [], anomalies: [] });
    monitorApi.systemUsageUsers.mockReset().mockResolvedValue({ items: [], total: 0, offset: 0, limit: 12, has_more: false });
    monitorApi.traces.mockReset().mockResolvedValue({ items: [] });
    monitorApi.traceGroups.mockReset().mockResolvedValue({ items: [], total: 0, offset: 0, limit: 24, has_more: false });
    monitorApi.traceGroup.mockReset().mockResolvedValue({ chain: null, traces: [], spans: [], events: [] });
    vi.stubGlobal("WebSocket", class { onopen?: () => void; onclose?: () => void; onmessage?: (event: MessageEvent) => void; close() { this.onclose?.(); } });
  });

  afterEach(() => { vi.unstubAllGlobals(); });

  it("restores the page named in the URL after the monitor is mounted", async () => {
    history.replaceState({}, "", "/monitor?page=traces");

    render(<MonitorApp />);

    expect(await screen.findByRole("heading", { name: "运行链路", level: 1 })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Token 与缓存" })).not.toBeInTheDocument();
  });

  it("writes the selected page to the URL so a browser refresh can restore it", async () => {
    history.replaceState({}, "", "/monitor");

    render(<MonitorApp />);

    await screen.findByRole("heading", { name: "系统总览", level: 1 });
    fireEvent.click(screen.getByRole("button", { name: "运行链路" }));

    expect(location.pathname).toBe("/monitor/traces");
    expect(location.search).toBe("");
  });

  it("does not expose a standalone user-session browser or load its metadata", async () => {
    history.replaceState({}, "", "/monitor");

    render(<MonitorApp />);

    expect(await screen.findByRole("heading", { name: "系统总览", level: 1 })).toBeVisible();
    expect(screen.queryByRole("button", { name: "用户会话" })).not.toBeInTheDocument();
    expect(monitorApi).not.toHaveProperty("sessions");
  });

  it("shows paginated problem-oriented trace groups and loads a chain detail on demand", async () => {
    history.replaceState({}, "", "/monitor/traces");
    monitorApi.traceGroups.mockResolvedValueOnce({
      items: [{
        chain_id: "workflow-42",
        chain_name: "文档处理",
        entrypoint: "/api/v1/chat",
        user_ids: ["alice"],
        workspace_ids: ["workspace-a"],
        started_at: "2026-09-05T10:00:00Z",
        last_seen: "2026-09-05T10:00:05Z",
        status: "error",
        trace_count: 2,
        error_count: 1,
        total_duration_ms: 4000,
        total_tokens: 200,
        sample_trace_id: "trace-1",
        error_kinds: ["TimeoutError"],
      }],
      total: 1,
      offset: 0,
      limit: 24,
      has_more: false,
    });
    monitorApi.traceGroup.mockResolvedValueOnce({
      chain: {
        chain_id: "workflow-42",
        chain_name: "文档处理",
        entrypoint: "/api/v1/chat",
        user_ids: ["alice"],
        workspace_ids: ["workspace-a"],
        started_at: "2026-09-05T10:00:00Z",
        last_seen: "2026-09-05T10:00:05Z",
        status: "error",
        trace_count: 2,
        error_count: 1,
        total_duration_ms: 4000,
        total_tokens: 200,
        sample_trace_id: "trace-1",
        error_kinds: ["TimeoutError"],
      },
      traces: [{ trace_id: "trace-1", request_id: "request-1", session_id: "session-1", turn_id: "turn-1", workspace_id: "workspace-a", user_id: "alice", channel: "web", source: "user", started_at: "2026-09-05T10:00:00Z", status: "error", input_tokens: 100, output_tokens: 100, cached_tokens: 0, cache_miss_tokens: 0, reasoning_tokens: 0, total_tokens: 200, attributes: {} }],
      spans: [{ trace_id: "trace-1", span_id: "span-1", parent_span_id: undefined, kind: "model", name: "openai.chat", started_at: "2026-09-05T10:00:01Z", status: "error", attempt: 1, total_tokens: 200, input_tokens: 100, output_tokens: 100, cached_tokens: 0, error_kind: "TimeoutError", attributes: { provider: "openai", model: "gpt-5.4" } }],
      events: [],
    });

    render(<MonitorApp />);

    expect(await screen.findByText("文档处理")).toBeVisible();
    expect(screen.getByText("文档处理").closest("button")).toHaveTextContent("alice");
    expect(screen.getByText("发现问题")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /文档处理.*TimeoutError/ }));

    expect(await screen.findByText("链路详情")).toBeVisible();
    expect(screen.getByText("openai.chat")).toBeVisible();
    expect(monitorApi.traceGroups).toHaveBeenCalledWith({ days: 30, limit: 24, offset: 0, focus: "all", query: "" });
    expect(monitorApi.traceGroup).toHaveBeenCalledWith("workflow-42");
  });

  it("does not preload flat trace rows on the paginated chain page", async () => {
    history.replaceState({}, "", "/monitor/traces");

    render(<MonitorApp />);

    expect(await screen.findByRole("heading", { name: "运行链路", level: 1 })).toBeVisible();
    expect(monitorApi.traces).not.toHaveBeenCalled();
  });

  it("loads live diagnostics only after entering the route and opens a scoped realtime connection", async () => {
    history.replaceState({}, "", "/monitor");
    monitorApi.events.mockResolvedValueOnce({ items: [{
      event_id: "event-1", timestamp: "2026-09-06T10:00:00Z", level: "error", name: "provider.failed",
      trace_id: "trace-1", session_id: "session-1", turn_id: "turn-1", payload: { provider: "openai", model: "gpt-5.4" },
    }] });

    render(<MonitorApp />);

    await screen.findByRole("heading", { name: "系统总览", level: 1 });
    expect(monitorApi.events).not.toHaveBeenCalled();
    expect(monitorApi.createWsTicket).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "实时诊断" }));

    expect(await screen.findByRole("heading", { name: "实时诊断", level: 1 })).toBeVisible();
    expect(await screen.findByText("provider.failed")).toBeVisible();
    expect(monitorApi.events).toHaveBeenCalledWith(100);
    expect(monitorApi.createWsTicket).toHaveBeenCalledTimes(1);
    expect(monitorApi.traces).not.toHaveBeenCalled();
  });

  it("opens dense data in a dedicated route instead of the overview page", async () => {
    history.replaceState({}, "", "/monitor");

    render(<MonitorApp />);

    await screen.findByRole("heading", { name: "系统总览", level: 1 });
    fireEvent.click(screen.getByRole("button", { name: "用量中心" }));

    expect(location.pathname).toBe("/monitor/usage");
    expect(await screen.findByRole("heading", { name: "Token 与缓存" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "系统总览", level: 1 })).not.toBeInTheDocument();
  });

  it("shows the Provider-measured KV cache hit rate in the usage summary", async () => {
    history.replaceState({}, "", "/monitor/usage");
    monitorApi.systemUsage.mockResolvedValueOnce({
      scope: "system", period_days: 30, from: "2026-08-09T00:00:00Z", to: "2026-09-08T00:00:00Z", granularity: "day",
      events: 2, priced_events: 2, unpriced_events: 0, credits_complete: true, credit_status: "complete", credits_micro: 100, priced_credits_micro: 100,
      tokens: { input_tokens: 200, cached_input_tokens: 80, cache_write_input_tokens: 0, output_tokens: 4, reasoning_output_tokens: 0, total_tokens: 204 },
      cache_hit_rate: 0.4, cache_input_tokens: 200, cache_cached_input_tokens: 80,
      breakdown: [], users: [], workspaces: [], providers: [], purposes: [], models: [],
    });

    render(<MonitorApp />);

    expect(await screen.findByText("KV Cache 命中率")).toBeVisible();
    expect(screen.getByText("40.0%")).toBeVisible();
    expect(screen.getByText("80 / 200 Provider 输入 Token")).toBeVisible();
  });

  it("does not derive a KV cache hit rate from the legacy Trace fallback", async () => {
    history.replaceState({}, "", "/monitor/usage");
    monitorApi.overview.mockResolvedValueOnce({
      requests: 2, errors: 0, error_rate: 0, period_days: 30, active_users: 1,
      active_workspaces: 1, active_sessions: 1,
      latency_ms: { p50: 0, p90: 0, p95: 0, p99: 0 },
      ttft_ms: { p50: 0, p90: 0, p95: 0, p99: 0 },
      tokens: { input_tokens: 200, cached_input_tokens: 80, total_tokens: 200 },
      runtime: {}, status_breakdown: [], tags: { channels: [], sources: [], span_kinds: [] },
      component_spans: [], span_kinds: [], models: [], error_groups: [],
      events_by_level: {}, event_names: [], top_users: [],
    });
    monitorApi.systemUsage.mockRejectedValue(new Error("usage unavailable"));

    render(<MonitorApp />);

    const rate = await screen.findByText("KV Cache 命中率");
    expect(rate.parentElement).toHaveTextContent("—");
    expect(rate.parentElement).not.toHaveTextContent("40.0%");
    expect(rate.parentElement).not.toHaveTextContent("80 / 200 输入 Token");
  });

  it("loads dependency health only on the component route and links an anomaly to traces", async () => {
    history.replaceState({}, "", "/monitor/components");
    monitorApi.dependencies.mockResolvedValueOnce({
      scope: "system", period_days: 30, from: "2026-09-04T08:00:00Z", to: "2026-09-04T10:00:00Z",
      summary: { requests: 12, component_calls: 18, errors: 2, component_errors: 3, error_rate: 2 / 12, active_users: 4, active_workspaces: 2, latency_ms: { p50: 120, p90: 900, p95: 1400, p99: 2200 }, ttft_ms: { p50: 40, p90: 120, p95: 180, p99: 260 }, total_tokens: 1200 },
      trend: [],
      components: [{ label: "model · gateway.model", kind: "model", name: "gateway.model", requests: 18, successes: 15, errors: 3, failed_requests: 3, error_rate: 3 / 18, retries: 2, total_tokens: 1200, latency_ms: { p50: 200, p90: 900, p95: 1400, p99: 2200 }, ttft_ms: { p50: 60, p90: 180, p95: 220, p99: 300 }, users: 4, workspaces: 2, error_kinds: ["TimeoutError"], first_seen: "2026-09-04T09:00:00Z", last_seen: "2026-09-04T10:00:00Z", status: "degraded" }],
      providers: [], models: [], anomalies: [{ type: "component", key: "model · gateway.model", status: "degraded", errors: 3, error_rate: 3 / 18, p95_ms: 1400, last_seen: "2026-09-04T10:00:00Z" }],
    });

    render(<MonitorApp />);

    expect(await screen.findByRole("heading", { name: "组件与模型", level: 1 })).toBeVisible();
    expect(await screen.findByText("gateway.model")).toBeVisible();
    expect(screen.getByText("依赖异常排名")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /model · gateway\.model/ }));
    expect(location.pathname).toBe("/monitor/traces");
    expect(location.search).toContain("focus=errors");
  });

  it("groups errors with impact metrics and links problems to filtered traces", async () => {
    history.replaceState({}, "", "/monitor/errors");
    monitorApi.errors.mockResolvedValueOnce({
      scope: "system", period_days: 30, from: "2026-09-04T08:00:00Z", to: "2026-09-04T10:00:00Z",
      summary: { error_groups: 1, total_errors: 3, affected_requests: 2, affected_users: 2, ongoing_groups: 1, recovered_groups: 0, error_rate: .1 }, trend: [], total: 1, offset: 0, limit: 12, has_more: false,
      items: [{ fingerprint: "TimeoutError|model|gateway.model", error_kind: "TimeoutError", kind: "model", name: "gateway.model", count: 3, trace_count: 2, affected_users: 2, affected_workspaces: 1, first_seen: "2026-09-04T09:00:00Z", last_seen: "2026-09-04T10:00:00Z", latency_ms: { p50: 900, p90: 1400, p95: 1400, p99: 1400 }, provider_models: ["openai / gpt-5.4"], chains: ["文档处理"], recovery_status: "ongoing", sample_trace_id: "trace-1" }],
    });

    render(<MonitorApp />);

    expect(await screen.findByRole("heading", { name: "错误分析", level: 1 })).toBeVisible();
    expect(await screen.findByText("影响用户")).toBeVisible();
    const problem = await screen.findByRole("button", { name: /TimeoutError.*gateway\.model/ });
    fireEvent.click(problem);
    expect(location.pathname).toBe("/monitor/traces");
    expect(location.search).toContain("focus=errors");
    expect(location.search).toContain("TimeoutError%7Cmodel%7Cgateway.model");
  });

  it("opens authorization audit inside the monitor plane", async () => {
    history.replaceState({}, "", "/monitor");

    render(<MonitorApp />);

    await screen.findByRole("heading", { name: "系统总览", level: 1 });
    fireEvent.click(screen.getByRole("button", { name: "审计日志" }));

    expect(await screen.findByRole("heading", { name: "审计日志", level: 2 })).toBeVisible();
    expect(monitorApi.authorizationAudit).toHaveBeenCalledWith({ limit: 20, offset: 0 });
  });

  it("offers direct monitor login when the shared session is missing", async () => {
    history.replaceState({}, "", "/monitor");
    authenticate.mockRejectedValueOnce({ status: 401 });

    render(<MonitorApp />);

    expect(await screen.findByRole("heading", { name: "登录监控平台" })).toBeVisible();
    fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "developer" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "test-password" } });
    fireEvent.click(screen.getByRole("button", { name: "登录监控平台" }));

    expect(monitorApi.login).toHaveBeenCalledWith("developer", "test-password");
    expect(await screen.findByRole("heading", { name: "系统总览", level: 1 })).toBeVisible();
  });

  it("explains that a signed-in account needs monitor permission", async () => {
    history.replaceState({}, "", "/monitor");
    authenticate.mockRejectedValueOnce({ status: 403 });

    render(<MonitorApp />);

    expect(await screen.findByText("当前账号没有监控权限，请联系管理员授权。"))
      .toBeVisible();
  });

  it("shows the all-user system overview with operational dimensions", async () => {
    history.replaceState({}, "", "/monitor");
    monitorApi.overview.mockResolvedValueOnce({
      requests: 24,
      successes: 22,
      errors: 2,
      error_rate: 2 / 24,
      failed_requests: 2,
      failure_rate: 2 / 24,
      period_days: 30,
      active_users: 6,
      active_workspaces: 2,
      active_sessions: 8,
      latency_ms: { p50: 120, p90: 360, p95: 480, p99: 720 },
      ttft_ms: { p50: 40, p90: 120, p95: 160, p99: 240 },
      tokens: { total_tokens: 100 },
      runtime: {},
      tags: { channels: [{ value: "web", requests: 24 }], sources: [], span_kinds: [] },
      status_breakdown: [{ value: "ok", requests: 22 }],
      component_spans: [],
      span_kinds: [],
      models: [],
      error_groups: [],
      events_by_level: {},
      event_names: [],
      top_users: [{ user_id: "alice", requests: 8, successes: 8, errors: 0, error_rate: 0, total_tokens: 40, avg_duration_ms: 100, workspaces: ["w1"], last_seen: "2026-09-04T10:00:00Z" }],
    });
    monitorApi.systemUsage.mockResolvedValueOnce({
      scope: "system", events: 24, priced_events: 24, unpriced_events: 0, credits_complete: true, credit_status: "complete", credits_micro: 1234, priced_credits_micro: 1234,
      tokens: { input_tokens: 70, cached_input_tokens: 10, cache_write_input_tokens: 2, output_tokens: 20, reasoning_output_tokens: 5, total_tokens: 95 },
      breakdown: [], users: [], workspaces: [], providers: [], purposes: [], models: [],
    });

    render(<MonitorApp />);

    expect(await screen.findByRole("heading", { name: "系统总览", level: 1 })).toBeVisible();
    expect(screen.getByText("活跃用户")).toBeVisible();
    expect(screen.getByText("健康信号")).toBeVisible();
    expect(screen.getByText("全用户负载")).toBeVisible();
    expect(screen.getByText("alice")).toBeVisible();
    expect(screen.getByText("P99 720 ms")).toBeVisible();
    expect(screen.getByText("P99 240 ms")).toBeVisible();
  });

  it("keeps the destructive reset action behind a low-emphasis overflow menu", async () => {
    history.replaceState({}, "", "/monitor");

    render(<MonitorApp />);

    await screen.findByRole("heading", { name: "系统总览", level: 1 });
    expect(screen.queryByRole("button", { name: "重置全部数据" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "更多监控操作" }));

    expect(screen.getByRole("button", { name: "重置全部数据" })).toBeVisible();
  });

  it("progressively reveals the user load list instead of mounting every user at once", async () => {
    history.replaceState({}, "", "/monitor");
    const users = Array.from({ length: 12 }, (_, index) => ({
      user_id: `user-${String(index + 1).padStart(2, "0")}`,
      requests: 12 - index,
      successes: 12 - index,
      errors: 0,
      error_rate: 0,
      total_tokens: 100,
      avg_duration_ms: 120,
      workspaces: ["workspace"],
      last_seen: "2026-09-04T10:00:00Z",
    }));
    monitorApi.overview.mockResolvedValueOnce({
      requests: 78,
      successes: 78,
      errors: 0,
      error_rate: 0,
      period_days: 30,
      active_users: users.length,
      active_workspaces: 1,
      active_sessions: users.length,
      latency_ms: { p50: 120, p90: 180, p95: 240, p99: 360 },
      ttft_ms: { p50: 40, p90: 60, p95: 80, p99: 120 },
      tokens: { total_tokens: 1200 },
      tags: { channels: [], sources: [], span_kinds: [] },
      status_breakdown: [{ value: "ok", requests: 78 }],
      component_spans: [], span_kinds: [], models: [], error_groups: [],
      events_by_level: {}, event_names: [], top_users: users,
    });

    render(<MonitorApp />);

    await screen.findByText("user-01");
    expect(screen.queryByText("user-12")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /加载更多用户/ }));
    expect(screen.getByText("user-09")).toBeVisible();
    expect(screen.queryByText("user-12")).not.toBeInTheDocument();
  });

  it("shows a visual activity story instead of a wall of empty cards", async () => {
    history.replaceState({}, "", "/monitor");
    monitorApi.overview.mockResolvedValueOnce({
      requests: 96,
      successes: 81,
      errors: 15,
      error_rate: 15 / 96,
      failed_requests: 15,
      failure_rate: 15 / 96,
      period_days: 30,
      active_users: 4,
      active_workspaces: 2,
      active_sessions: 8,
      latency_ms: { p50: 860, p90: 2200, p95: 3180, p99: 4800 },
      ttft_ms: { p50: 150, p90: 360, p95: 480, p99: 720 },
      tokens: { total_tokens: 250000 },
      runtime: { queue_size: 18, queue_capacity: 5000, active_traces: 2, dropped_events: 0, live_subscribers: 1 },
      tags: { channels: [{ value: "web", requests: 30 }], sources: [{ value: "demo", requests: 96 }], span_kinds: [{ value: "model", requests: 96 }] },
      status_breakdown: [{ value: "ok", requests: 81 }, { value: "error", requests: 10 }, { value: "timeout", requests: 5 }],
      component_spans: [{ name: "model.invoke", label: "model · model.invoke", requests: 96, successes: 81, errors: 15, failed_requests: 15, error_rate: 15 / 96, failure_rate: 15 / 96, retries: 16, avg_duration_ms: 420, total_tokens: 250000 }],
      span_kinds: [], models: [], error_groups: [], events_by_level: { info: 96, warning: 20, error: 15 },
      event_names: [{ value: "request.completed", requests: 96 }],
      top_users: [{ user_id: "demo-user-alice", requests: 30, successes: 27, errors: 3, error_rate: .1, total_tokens: 60000, avg_duration_ms: 780, workspaces: ["demo-workspace-lab"], last_seen: "2026-09-04T10:00:00Z" }],
    });
    monitorApi.systemUsage.mockResolvedValueOnce({
      scope: "system", period_days: 30, from: "2026-08-05T00:00:00Z", to: "2026-09-04T00:00:00Z", granularity: "day", events: 96, priced_events: 80, unpriced_events: 16, credits_complete: false, credit_status: "partial", credits_micro: null, priced_credits_micro: 320000,
      tokens: { total_tokens: 250000 },
      breakdown: [
        { day: "2026-09-02", events: 24, total_tokens: 52000 },
        { day: "2026-09-03", events: 32, total_tokens: 87000 },
        { day: "2026-09-04", events: 40, total_tokens: 111000 },
      ], users: [], workspaces: [], providers: [], purposes: [], models: [],
    });

    render(<MonitorApp />);

    expect(await screen.findByText("请求节奏")).toBeVisible();
    expect(screen.getByRole("img", { name: "按日请求与 Token 趋势" })).toBeVisible();
    const latestPoint = screen.getByRole("button", { name: /2026-09-04.*40 次请求.*111,000 Token/ });
    expect(latestPoint).toBeVisible();
    fireEvent.mouseEnter(latestPoint);
    expect(screen.getByRole("status")).toHaveTextContent("2026-09-04");
    expect(screen.getByRole("status")).toHaveTextContent("40 次请求");
    expect(screen.getByRole("status")).toHaveTextContent("111,000 Token");
    fireEvent.mouseLeave(latestPoint);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    fireEvent.focus(latestPoint);
    expect(screen.getByRole("status")).toHaveTextContent("2026-09-04");
    expect(screen.getByRole("status").parentElement).toHaveClass("mon-chart-stage");
    expect(screen.getByRole("status")).toHaveAttribute("data-placement", "below");
    expect(screen.getByRole("status").style.left).toBe("90%");
    fireEvent.blur(latestPoint);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    const lowPoint = screen.getByRole("button", { name: /2026-09-02.*24 次请求.*52,000 Token/ });
    fireEvent.focus(lowPoint);
    expect(screen.getByRole("status")).toHaveAttribute("data-placement", "above");
    fireEvent.blur(lowPoint);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByText("健康信号")).toBeVisible();
    expect(screen.getByText("全用户负载")).toBeVisible();
    expect(screen.getByText("总入口")).toBeVisible();
    expect(screen.getByText("依赖调用")).toBeVisible();
    expect(screen.getByText("错误事件")).toBeVisible();
  });

  it("keeps the overview usable when a usage breakdown item has no token object", async () => {
    history.replaceState({}, "", "/monitor/usage");
    monitorApi.systemUsage.mockResolvedValueOnce({
      scope: "system", events: 1, priced_events: 0, unpriced_events: 1, credits_complete: false, credit_status: "incomplete", credits_micro: null, priced_credits_micro: 0,
      tokens: {},
      breakdown: [{ day: "2026-09-04", total_tokens: 12 }],
      users: [], workspaces: [], providers: [], purposes: [], models: [],
    } as never);

    render(<MonitorApp />);

    expect(await screen.findByText("Token 与缓存")).toBeVisible();
    expect(screen.getByRole("button", { name: /2026-09-04.*12 Token/ })).toBeVisible();
  });

  it("loads usage users by page instead of mounting the whole all-user ledger", async () => {
    history.replaceState({}, "", "/monitor/usage");
    const users = Array.from({ length: 13 }, (_, index) => ({
      user_id: `usage-user-${String(index + 1).padStart(2, "0")}`,
      events: 13 - index,
      priced_events: 13 - index,
      unpriced_events: 0,
      credits_complete: true,
      credit_status: "complete",
      credits_micro: 100,
      priced_credits_micro: 100,
      tokens: { total_tokens: 1000 + index },
    }));
    monitorApi.systemUsageUsers
      .mockResolvedValueOnce({ items: users.slice(0, 12), total: users.length, offset: 0, limit: 12, has_more: true })
      .mockResolvedValueOnce({ items: users.slice(12), total: users.length, offset: 12, limit: 12, has_more: false });

    render(<MonitorApp />);

    expect(await screen.findByText("usage-user-01")).toBeVisible();
    expect(screen.queryByText("usage-user-13")).not.toBeInTheDocument();
    expect(monitorApi.systemUsageUsers).toHaveBeenCalledWith(30, 12, 0);
    fireEvent.click(screen.getByRole("button", { name: /加载更多用户/ }));
    expect(await screen.findByText("usage-user-13")).toBeVisible();
    expect(monitorApi.systemUsageUsers).toHaveBeenCalledWith(30, 12, 12);
  });

  it("uses the shared provider/model catalog and refreshes a five-minute sliding trend", async () => {
    history.replaceState({}, "", "/monitor/usage");
    monitorApi.systemUsage.mockResolvedValueOnce({
      scope: "system", period_days: 30, from: "2026-08-05T00:00:00Z", to: "2026-09-04T00:00:00Z", granularity: "day", events: 3, priced_events: 3, unpriced_events: 0, credits_complete: true, credit_status: "complete", credits_micro: 1000, priced_credits_micro: 1000,
      tokens: { total_tokens: 3000 }, breakdown: [], users: [], workspaces: [],
      providers: [{ provider: "openai", events: 3, priced_events: 3, unpriced_events: 0, credits_complete: true, credit_status: "complete", credits_micro: 1000, priced_credits_micro: 1000, tokens: { total_tokens: 3000 } }],
      purposes: [], models: [{ provider_model: "gpt-5.4", provider: "openai", events: 3, priced_events: 3, unpriced_events: 0, credits_complete: true, credit_status: "complete", credits_micro: 1000, priced_credits_micro: 1000, tokens: { total_tokens: 3000 } }],
      catalog: { providers: { openai: { adapter: "openai_compatible", api_key_configured: true } }, models: { "gpt-5.4": { provider: "openai", model_id: "gpt-5.4", profile_names: ["default"] } } },
    } as never);
    monitorApi.systemUsageTrend.mockResolvedValueOnce({
      scope: "system", period_days: 1, from: "2026-09-04T08:00:00Z", to: "2026-09-04T10:00:00Z", granularity: "five_minute", events: 3, priced_events: 3, unpriced_events: 0, credits_complete: true, credit_status: "complete", credits_micro: 1000, priced_credits_micro: 1000, tokens: { total_tokens: 3000 }, users: [], workspaces: [], providers: [], purposes: [], models: [],
      breakdown: [{ day: "2026-09-04T09:55:00+00:00", period_start: "2026-09-04T09:55:00+00:00", granularity: "five_minute", events: 3, total_tokens: 3000 }],
    } as never);

    render(<MonitorApp />);

    expect(await screen.findByText("Token 与缓存")).toBeVisible();
    expect(screen.getByText("来自多模型厂商管理")).toBeVisible();
    expect(screen.getAllByText(/openai_compatible/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/default/).length).toBeGreaterThan(0);
    await waitFor(() => expect(monitorApi.systemUsageTrend).toHaveBeenCalledWith(120, 5));
    expect(await screen.findByRole("img", { name: "最近 120 分钟 Token 趋势" })).toBeVisible();
    expect(await screen.findByRole("button", { name: /2026-09-04 09:55.*3 次事件.*3,000 Token/ })).toBeVisible();
  });
});
