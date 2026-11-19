import { fireEvent, render, screen } from "@testing-library/react";

import { ActivityPanel } from "./ActivityPanel";

describe("ActivityPanel", () => {
  it("does not crash when a replayed activity has an unknown kind", () => {
    render(<ActivityPanel showReasoning={false} running={false} activities={[{
      id: "unknown", kind: "future-tool" as never, label: "未知工具事件", status: "completed", startedAt: "2026-07-19T00:00:00Z", completedAt: "2026-07-19T00:00:02Z",
    }]} />);
    const trigger = screen.getByRole("button", { name: /已处理 2s/ });
    expect(trigger).toBeVisible();
    fireEvent.click(trigger);
    expect(screen.getByText("未知工具事件")).toBeVisible();
  });

  it("uses the full turn duration instead of a completed tool's zero-length event", () => {
    render(<ActivityPanel
      showReasoning={false}
      running={false}
      startedAt="2026-07-19T00:00:00Z"
      completedAt="2026-07-19T00:00:05Z"
      activities={[{ id: "tool", kind: "tool", label: "工具调用完成", status: "completed", startedAt: "2026-07-19T00:00:02Z", completedAt: "2026-07-19T00:00:02Z" }]}
    />);

    expect(screen.getByRole("button", { name: /已处理 5s/ })).toBeVisible();
  });

  it("shows live search headline while running without content and auto-collapses once answer streams", () => {
    const activities = [
      {
        id: "step:c1",
        kind: "commentary" as const,
        label: "正在检索深圳峰会最新议程",
        status: "completed" as const,
        startedAt: "2026-11-19T03:00:00Z",
        completedAt: "2026-11-19T03:00:01Z",
      },
      {
        id: "step:s1",
        kind: "search" as const,
        label: "正在搜索「APEC 2026 深圳」",
        status: "running" as const,
        startedAt: "2026-11-19T03:00:01Z",
      },
    ];

    const { rerender } = render(
      <ActivityPanel
        showReasoning={false}
        running={true}
        hasContent={false}
        summary="正在搜索「APEC 2026 深圳」"
        startedAt="2026-11-19T03:00:00Z"
        activities={activities}
      />,
    );

    const trigger = screen.getByRole("button", { name: /正在搜索「APEC 2026 深圳」/ });
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("正在检索深圳峰会最新议程")).toBeVisible();

    // Once final answer starts streaming, auto-collapse while staying expandable
    rerender(
      <ActivityPanel
        showReasoning={false}
        running={true}
        hasContent={true}
        startedAt="2026-11-19T03:00:00Z"
        activities={activities.map((item) => ({ ...item, status: "completed" as const }))}
      />,
    );
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("正在检索深圳峰会最新议程")).toBeVisible();
  });
});
