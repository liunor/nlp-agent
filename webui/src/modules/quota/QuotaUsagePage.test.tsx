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

  it("shows the lowest remaining percentage across every active bucket", async () => {
    render(<QuotaUsagePage />);

    expect(await screen.findByText("25%")).toHaveAttribute("title", "可用 30 μcredits");
    expect(screen.getByText("80%")).toBeInTheDocument();
    expect(screen.getByText("40%")).toBeInTheDocument();
    const daily = screen.getByRole("progressbar", { name: "个人 今日剩余额度" });
    const weekly = screen.getByRole("progressbar", { name: "个人 本周剩余额度" });
    expect(daily.querySelector("i")).toHaveStyle({ width: "80%" });
    expect(weekly.querySelector("i")).toHaveStyle({ width: "40%" });
    expect(screen.queryByText("工作空间 · 今日")).not.toBeInTheDocument();
  });

  it("keeps daily and weekly personal quota slots visible when one period is not configured", async () => {
    getQuota.mockResolvedValue({
      quota: {
        user_id: "user-1",
        workspace_id: "workspace-a",
        buckets: [{ owner_type: "user", owner_id: "user-1", bucket_type: "daily", limit_micro: 100, grant_micro: 0, adjustment_micro: 0, consumed_micro: 0, reserved_micro: 20, remaining_micro: 80, reset_at: "2026-08-31T00:00:00+00:00", over_limit: false }],
      },
      policy: null,
    });

    render(<QuotaUsagePage />);

    expect(await screen.findByText("个人 · 今日")).toBeInTheDocument();
    expect(screen.getByText("个人 · 本周")).toBeInTheDocument();
    expect(screen.getByText("未单独设置")).toBeInTheDocument();
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

    await waitFor(() => expect(screen.getByText("25%")).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent("部分用量数据加载失败");
  });

  it("rounds the real remaining balance to an integer percentage and shows the server reset time", async () => {
    getQuota.mockResolvedValue({
      quota: { user_id: "user-1", workspace_id: null, buckets: [
        { owner_type: "user", owner_id: "user-1", bucket_type: "daily", limit_micro: 3000000, grant_micro: 0, adjustment_micro: 0, consumed_micro: 2381, reserved_micro: 0, remaining_micro: 2997619, reset_at: "2026-10-13T00:00:00+00:00", over_limit: false },
      ] }, policy: null,
    });
    render(<QuotaUsagePage />);
    expect((await screen.findAllByText("100%")).length).toBe(2);
    expect(screen.queryByText("99.92%")).not.toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "个人 今日剩余额度" }).querySelector("i")).toHaveStyle({ width: `${2997619 / 3000000 * 100}%` });
    expect(document.querySelector("time")).toHaveAttribute("datetime", "2026-10-13T00:00:00+00:00");
    expect(document.querySelector("time")).toHaveTextContent("重置");
  });

  it("includes grants, adjustments, and reservations in the remaining percentage", async () => {
    getQuota.mockResolvedValue({
      quota: { user_id: "user-1", workspace_id: null, buckets: [
        { owner_type: "user", owner_id: "user-1", bucket_type: "daily", limit_micro: 1000, grant_micro: 1000, adjustment_micro: -200, consumed_micro: 840, reserved_micro: 450, remaining_micro: 510, reset_at: "2026-10-13T00:00:00+00:00", over_limit: false },
      ] }, policy: null,
    });
    render(<QuotaUsagePage />);
    expect((await screen.findAllByText("28%")).length).toBe(2);
  });

  it.each([
    { limit: 0, remaining: 0 },
    { limit: 100, remaining: -5 },
  ])("shows zero remaining percentage for an exhausted quota (%j)", async ({ limit, remaining }) => {
    getQuota.mockResolvedValue({
      quota: { user_id: "user-1", workspace_id: null, buckets: [
        { owner_type: "user", owner_id: "user-1", bucket_type: "daily", limit_micro: limit, grant_micro: 0, adjustment_micro: 0, consumed_micro: limit - remaining, reserved_micro: 0, remaining_micro: remaining, reset_at: "2026-10-13T00:00:00+00:00", over_limit: remaining < 0 },
      ] }, policy: null,
    });
    render(<QuotaUsagePage />);
    expect((await screen.findAllByText("0%")).length).toBe(2);
    expect(screen.getByRole("progressbar", { name: "个人 今日剩余额度" }).querySelector("i")).toHaveStyle({ width: "0%" });
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
