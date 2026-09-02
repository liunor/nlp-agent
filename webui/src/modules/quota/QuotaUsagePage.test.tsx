import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { QuotaUsagePage } from "./QuotaUsagePage";

const { getQuota, getUsage } = vi.hoisted(() => ({
  getQuota: vi.fn(),
  getUsage: vi.fn(),
}));

vi.mock("@/platform/http/api", () => ({
  api: {
    getQuota,
    getUsage,
  },
}));

vi.mock("@/platform/auth/AuthContext", () => ({
  useOptionalAuth: () => ({
    user: {
      user_id: "user-1",
      workspace_ids: ["workspace-a", "workspace-b"],
    },
  }),
}));

vi.mock("@/platform/realtime/client", () => ({
  StudentSocket: class {
    connect() {}
    close() {}
  },
}));

describe("QuotaUsagePage", () => {
  beforeEach(() => {
    getQuota.mockResolvedValue({
      quota: {
        user_id: "user-1",
        workspace_id: "workspace-a",
        buckets: [
          { owner_type: "user", owner_id: "user-1", bucket_type: "daily", limit_micro: 100, grant_micro: 0, adjustment_micro: 0, consumed_micro: 0, reserved_micro: 20, remaining_micro: 80, reset_at: "2026-08-31T00:00:00+00:00", over_limit: false },
          { owner_type: "user", owner_id: "user-1", bucket_type: "weekly", limit_micro: 500, grant_micro: 0, adjustment_micro: 0, consumed_micro: 0, reserved_micro: 300, remaining_micro: 200, reset_at: "2026-08-31T00:00:00+00:00", over_limit: false },
          { owner_type: "workspace", owner_id: "workspace-a", bucket_type: "daily", limit_micro: 120, grant_micro: 0, adjustment_micro: 0, consumed_micro: 0, reserved_micro: 90, remaining_micro: 30, reset_at: "2026-08-31T00:00:00+00:00", over_limit: false },
        ],
      },
      policy: null,
    });
    getUsage.mockImplementation(async (_days: number, _workspace: string, granularity: string) => ({
      events: 12,
      priced_credits_micro: 0,
      granularity,
      breakdown: [
        { day: "2026-08-30", period_start: granularity === "week" ? "2026-08-24T00:00:00+00:00" : "2026-08-30T00:00:00+00:00", period_end: granularity === "week" ? "2026-08-31T00:00:00+00:00" : "2026-08-31T00:00:00+00:00", purpose: "coordinator", provider: "openai", provider_model: "gpt-5", events: 2, priced_events: 2, unpriced_events: 0, priced_credits_micro: 42, total_tokens: 120 },
      ],
    }));
  });

  it("shows the minimum effective remaining balance across every active bucket", async () => {
    render(<QuotaUsagePage />);

    await waitFor(() => expect(screen.getAllByText("30 μcredits").length).toBeGreaterThan(0));
    expect(screen.getByText("30 μcredits")).toBeInTheDocument();
    expect(screen.queryByText("310 μcredits")).not.toBeInTheDocument();
    expect(screen.queryByText("工作空间 · 今日")).not.toBeInTheDocument();
  });

  it("renders unlimited quota as text instead of exposing the internal sentinel", async () => {
    getQuota.mockResolvedValue({
      quota: {
        user_id: "user-1",
        workspace_id: "workspace-a",
        buckets: [{ owner_type: "user", owner_id: "user-1", bucket_type: "daily", limit_micro: null, grant_micro: 0, adjustment_micro: 0, consumed_micro: 0, reserved_micro: 0, remaining_micro: Number("9223372036854775807"), reset_at: "2026-08-31T00:00:00+00:00", over_limit: false }],
      },
      policy: null,
    });

    render(<QuotaUsagePage />);

    await waitFor(() => expect(screen.getAllByText("无限").length).toBeGreaterThan(0));
    expect(screen.queryByText("9,223,372,036,854,775,807 μcredits")).not.toBeInTheDocument();
  });

  it("keeps the balance visible when token activity fails independently", async () => {
    getUsage.mockImplementation(async (_days: number, _workspace: string, granularity: string) => {
      if (granularity === "week") throw new Error("weekly activity unavailable");
      return { events: 12, priced_credits_micro: 0, granularity, breakdown: [] };
    });

    render(<QuotaUsagePage />);

    await waitFor(() => expect(screen.getByText("30 μcredits")).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent("部分用量数据加载失败");
  });

  it("keeps the workspace scope internal without exposing a selector", async () => {
    render(<QuotaUsagePage />);

    await screen.findByText("近 7 天请求");
    expect(getQuota).toHaveBeenCalledWith("workspace-a");
    expect(getUsage).toHaveBeenCalledWith(182, "workspace-a", "day");
    expect(getUsage).toHaveBeenCalledWith(182, "workspace-a", "week");
    expect(screen.queryByLabelText("工作空间")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("uses a compact activity view instead of verbose policy and call details", async () => {
    render(<QuotaUsagePage />);

    expect(await screen.findByText("Token 活动")).toBeInTheDocument();
    expect(screen.queryByText("ACCOUNT RESOURCE")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "额度与用量" })).not.toBeInTheDocument();
    expect(screen.queryByText("查看当前账号在不同工作空间中的额度、用量与账务状态。")).not.toBeInTheDocument();
    expect(screen.getByText("近 7 天请求")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.queryByText("30 天已用")).not.toBeInTheDocument();
    expect(screen.queryByText("策略来源")).not.toBeInTheDocument();
    expect(screen.queryByText("调用明细")).not.toBeInTheDocument();
    expect(screen.getByRole("grid", { name: "按日查看 Token 活动" })).toBeInTheDocument();
  });

  it("renders individual token cells with hover details and a compact usage summary", async () => {
    render(<QuotaUsagePage />);

    expect(await screen.findByRole("grid", { name: "按日查看 Token 活动" })).toBeInTheDocument();
    expect(screen.getByText("近 6 个月")).toBeInTheDocument();
    expect(screen.getAllByRole("gridcell")).toHaveLength(182);
    const dailyCell = screen.getByRole("gridcell", { name: "8月30日 使用了 120 个 Token" });
    expect(dailyCell).not.toHaveAttribute("title");
    expect(dailyCell).toHaveAttribute("data-tooltip", "8月30日 使用了 120 个 Token");
    expect(screen.getByText("活跃天数")).toBeInTheDocument();
    expect(screen.getByText("单日峰值")).toBeInTheDocument();
    expect(screen.getByText("最近使用")).toBeInTheDocument();
  });

  it("switches the activity grid to weekly periods", async () => {
    render(<QuotaUsagePage />);

    await screen.findByRole("grid", { name: "按日查看 Token 活动" });
    fireEvent.click(screen.getByRole("button", { name: "周" }));

    expect(await screen.findByRole("grid", { name: "按周查看 Token 活动" })).toBeInTheDocument();
    expect(screen.getByText("近 26 周")).toBeInTheDocument();
    expect(screen.getAllByRole("gridcell")).toHaveLength(26);
    expect(document.querySelectorAll(".quota-activity-week-column")).toHaveLength(26);
    expect(document.querySelectorAll(".quota-activity-week-column:first-child .quota-activity-week-square")).toHaveLength(7);
    const weeklyColumn = screen.getByRole("gridcell", { name: "8月24日–8月30日 使用了 120 个 Token" });
    expect(weeklyColumn).not.toHaveAttribute("title");
    expect(weeklyColumn).toHaveAttribute("data-tooltip", "8月24日–8月30日 使用了 120 个 Token");
  });
});
