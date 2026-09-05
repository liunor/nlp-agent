import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { RuntimeSettings } from "./DeveloperWorkspace";

const { getDeveloperHealthMock } = vi.hoisted(() => ({
  getDeveloperHealthMock: vi.fn(),
}));
vi.mock("@/platform/http/api", () => ({
  api: {
    getDeveloperHealth: getDeveloperHealthMock,
  },
}));

const snapshot = {
  runtime: {
    status: "ok",
    started: true,
    accepting_turns: true,
    active_turns: 2,
    subscribers: 1,
    database: ".data/gateway.sqlite3",
    durable_events: 42,
  },
  web: {
    host: "127.0.0.1",
    port: 8765,
    protocol: { http: "/api/v1", websocket: "/ws/v1" },
  },
  workspace: {
    roots: [
      { name: "sessions", path: ".data/sessions", exists: false, writable: false },
      { name: "tool-audit", path: ".data/tool-audit", exists: true, writable: true },
    ],
  },
};

describe("RuntimeSettings", () => {
  beforeEach(() => {
    getDeveloperHealthMock.mockReset();
    getDeveloperHealthMock.mockResolvedValue(snapshot.runtime);
  });

  it("explains runtime health, endpoint and on-demand storage states", () => {
    render(<RuntimeSettings snapshot={snapshot as never} />);

    expect(screen.getByRole("heading", { name: "运行诊断" })).toBeVisible();
    expect(screen.getByText("Runtime 正常")).toBeVisible();
    expect(screen.getByText(/正在接收请求/)).toBeVisible();
    expect(screen.getByText("当前活跃 Turn")).toBeVisible();
    expect(screen.getByText("2")).toBeVisible();
    expect(screen.getByText("按需创建")).toBeVisible();
    expect(screen.getByText("可写")).toBeVisible();
    expect(screen.getByText("127.0.0.1:8765")).toBeVisible();
    expect(screen.getByText("查看原始快照")).toBeVisible();
  });

  it("refreshes the lightweight health check without rebuilding the whole snapshot", async () => {
    getDeveloperHealthMock.mockResolvedValue({
      ...snapshot.runtime,
      active_turns: 5,
      durable_events: 49,
    });
    render(<RuntimeSettings snapshot={snapshot as never} />);

    fireEvent.click(screen.getByRole("button", { name: "重新检查" }));

    await waitFor(() => expect(screen.getByText("5")).toBeVisible());
    expect(screen.getByText("49")).toBeVisible();
    expect(getDeveloperHealthMock).toHaveBeenCalledOnce();
  });
});
