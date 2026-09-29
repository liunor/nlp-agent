import { fireEvent, render, screen } from "@testing-library/react";

import { MonitorStoragePage } from "./MonitorDashboardPages";

describe("MonitorStoragePage", () => {
  it("shows a compact retention status instead of a raw JSON wall", () => {
    render(
      <MonitorStoragePage
        storage={{ database: "mysql", database_bytes: 4_194_304, records: 321, traces: 80, spans: 180, events: 61, retention: { enabled: true, trace_days: 30, event_days: 14, interval_s: 2_592_000 } }}
        retentionDays={30}
        onPrune={async () => undefined}
      />,
    );

    expect(screen.getByText("数据留存")).toBeVisible();
    expect(screen.getByText("321")).toBeVisible();
    expect(screen.getByText("4.0 MB")).toBeVisible();
    expect(screen.getByText("Trace / Span")).toBeVisible();
    expect(screen.queryByText('"database": "mysql"')).not.toBeInTheDocument();
  });

  it("keeps manual cleanup behind an explicit confirmation", async () => {
    const onPrune = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));

    render(
      <MonitorStoragePage
        storage={{ traces: 1, spans: 2, events: 3, retention: { enabled: true, trace_days: 30, event_days: 30 } }}
        retentionDays={30}
        onPrune={onPrune}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /立即清理/ }));
    expect(onPrune).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
  });
});
