import { fireEvent, render, screen } from "@testing-library/react";
import type { TelemetryEvent } from "./api";
import { LiveDiagnosticsPage, filterDiagnosticEvents, groupDiagnosticEvents, safeEventContext } from "./LiveDiagnosticsPage";

const event = (id: string, overrides: Partial<TelemetryEvent> = {}): TelemetryEvent => ({
  event_id: id,
  timestamp: "2026-09-06T10:00:00Z",
  level: "warning",
  name: "request.retry",
  trace_id: "trace-1",
  session_id: "session-1",
  turn_id: "turn-1",
  worker_id: "worker-1",
  payload: { provider: "openai", model: "gpt-5.4", attempt: 2 },
  ...overrides,
});

describe("LiveDiagnosticsPage", () => {
  it("keeps actionable warning/error signals and excludes routine info events by default", () => {
    const visible = filterDiagnosticEvents([
      event("retry"),
      event("failure", { level: "error", name: "provider.failed" }),
      event("slow", { level: "info", name: "request.slow" }),
      event("duration", { level: "info", name: "request.completed", payload: { duration_ms: 1800 } }),
      event("complete", { level: "info", name: "request.completed" }),
    ], "all", "");

    expect(visible.map((item) => item.event_id)).toEqual(["retry", "failure", "slow", "duration"]);
  });

  it("groups unlinked events by signal instead of mounting one card per event", () => {
    const groups = groupDiagnosticEvents([
      event("unlinked-1", { trace_id: undefined, name: "queue.backpressure" }),
      event("unlinked-2", { trace_id: undefined, name: "queue.backpressure" }),
      event("trace-1"),
    ]);

    expect(groups).toHaveLength(2);
    expect(groups.find((group) => !group.traceId)?.events).toHaveLength(2);
  });

  it("only exposes safe diagnostic context and never renders raw payload secrets", () => {
    const item = event("safe", { payload: { provider: "openai", model: "gpt-5.4", prompt: "private", api_key: "secret" } });

    expect(safeEventContext(item).join(" ")).toContain("openai");
    expect(safeEventContext(item).join(" ")).not.toContain("private");
    expect(safeEventContext(item).join(" ")).not.toContain("secret");

    render(<LiveDiagnosticsPage events={[item]} live onRefresh={() => undefined} onOpenTrace={() => undefined} />);

    expect(screen.getByRole("heading", { name: "实时诊断", level: 2 })).toBeVisible();
    expect(screen.getByText("查看运行链路")).toBeVisible();
    expect(screen.queryByText("private")).not.toBeInTheDocument();
    expect(screen.queryByText("secret")).not.toBeInTheDocument();
  });

  it("opens the exact trace from a diagnostic group", () => {
    const onOpenTrace = vi.fn();
    render(<LiveDiagnosticsPage events={[event("trace")]} live onRefresh={() => undefined} onOpenTrace={onOpenTrace} />);

    fireEvent.click(screen.getByRole("button", { name: "查看运行链路 trace-1" }));

    expect(onOpenTrace).toHaveBeenCalledWith("trace-1");
  });
});
