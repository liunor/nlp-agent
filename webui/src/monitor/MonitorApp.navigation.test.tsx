import { fireEvent, render, screen } from "@testing-library/react";

const { monitorApi } = vi.hoisted(() => ({
  monitorApi: {
    createWsTicket: vi.fn().mockResolvedValue({ ticket: "test-ticket", expires_in: 60 }),
    overview: vi.fn().mockResolvedValue({ requests: 0, errors: 0, error_rate: 0, period_days: 30, latency_ms: { p50: 0, p95: 0 }, ttft_ms: { p50: 0, p95: 0 }, tokens: {} }),
    traces: vi.fn().mockResolvedValue({ items: [] }), usage: vi.fn().mockResolvedValue({ items: [] }),
    sessions: vi.fn().mockResolvedValue({ items: [] }), errors: vi.fn().mockResolvedValue({ items: [] }),
    events: vi.fn().mockResolvedValue({ items: [] }), storage: vi.fn().mockResolvedValue({}),
    authorizationAudit: vi.fn().mockResolvedValue({ items: [], total: 0, offset: 0, limit: 50, has_more: false }),
    authorizationAuditStats: vi.fn().mockResolvedValue({ period_days: 30, since: "2026-08-01T00:00:00", total: 0, by_decision: {}, top_reasons: [] }),
  },
}));

vi.mock("./api", () => ({ authenticate: vi.fn().mockResolvedValue({}), monitorApi }));

import { MonitorApp } from "./MonitorApp";

describe("MonitorApp navigation", () => {
  beforeEach(() => {
    vi.stubGlobal("WebSocket", class { onopen?: () => void; onclose?: () => void; onmessage?: (event: MessageEvent) => void; close() { this.onclose?.(); } });
  });

  afterEach(() => { vi.unstubAllGlobals(); });

  it("restores the page named in the URL after the monitor is mounted", async () => {
    history.replaceState({}, "", "/monitor?page=traces");

    render(<MonitorApp />);

    expect(await screen.findByRole("heading", { name: "运行链路" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Token 与缓存" })).not.toBeInTheDocument();
  });

  it("writes the selected page to the URL so a browser refresh can restore it", async () => {
    history.replaceState({}, "", "/monitor");

    render(<MonitorApp />);

    await screen.findByRole("heading", { name: "Token 与缓存" });
    fireEvent.click(screen.getByRole("button", { name: "运行记录" }));

    expect(location.search).toBe("?page=traces");
  });

  it("opens authorization audit inside the monitor plane", async () => {
    history.replaceState({}, "", "/monitor");

    render(<MonitorApp />);

    await screen.findByRole("heading", { name: "Token 与缓存" });
    fireEvent.click(screen.getByRole("button", { name: "审计日志" }));

    expect(await screen.findByRole("heading", { name: "审计日志", level: 2 })).toBeVisible();
    expect(monitorApi.authorizationAudit).toHaveBeenCalledWith({ limit: 50, offset: 0 });
  });
});
