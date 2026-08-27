import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { DeveloperWorkspace } from "./DeveloperWorkspace";

const { ensureAuthMock, listVisibleMenusMock, getDeveloperSnapshotMock, listFeedbackMock, getFeedbackMock, markFeedbackReadMock } = vi.hoisted(() => ({
  ensureAuthMock: vi.fn(),
  listVisibleMenusMock: vi.fn(),
  getDeveloperSnapshotMock: vi.fn(),
  listFeedbackMock: vi.fn(),
  getFeedbackMock: vi.fn(),
  markFeedbackReadMock: vi.fn(async () => ({ ok: true })),
}));
vi.mock("@/platform/http/api", () => ({
  api: {
    listVisibleMenus: listVisibleMenusMock,
    getDeveloperSnapshot: getDeveloperSnapshotMock,
    listFeedback: listFeedbackMock,
    getFeedback: getFeedbackMock,
    markFeedbackRead: markFeedbackReadMock,
  },
  ensureAuth: ensureAuthMock,
}));

const menu = (routePath: string | null) => ({
  id: routePath ?? "root",
  parent_id: null,
  type: "page",
  name: routePath ?? "",
  route_path: routePath,
  component_key: null,
  permission_id: null,
  client_scope: "developer",
  sort_order: 10,
  visible: true,
  status: "active",
});

const ALL_ROUTES = ["/developer", "/developer/agents", "/developer/tools", "/developer/models", "/developer/mcp", "/developer/skills", "/developer/release-notes", "/developer/automations", "/developer/feedback", "/developer/settings", "/developer/users", "/developer/roles", "/developer/menus", "/developer/audit", "/developer/sessions"];

const snapshot = {
  runtime: { status: "ok", active_turns: 0, durable_events: 0 },
  features: {},
  models: { defaults: {}, routes: {}, models: {}, presets: {}, providers: {} },
  tools: { catalog_revision: 1, items: [], policies: {}, mcp_servers: {}, custom: {} },
  skills: [],
  agents: {},
  workspace: { roots: [] },
  web: {},
};

describe("DeveloperWorkspace shell access", () => {
  beforeEach(() => {
    ensureAuthMock.mockReset();
    listVisibleMenusMock.mockReset();
    getDeveloperSnapshotMock.mockReset();
    listFeedbackMock.mockReset();
    getFeedbackMock.mockReset();
    markFeedbackReadMock.mockClear();
    ensureAuthMock.mockResolvedValue({ roles: ["custom"], permissions: ["learning:feedback:read"] });
    listFeedbackMock.mockResolvedValue({ items: [], total: 0 });
    getFeedbackMock.mockResolvedValue({ thread_id: "t1", user_id: "u1", username: "alice", display_name: "Alice", status: "open", category: "other", priority: "medium", messages: [] });
    history.replaceState({}, "", "/developer");
  });

  it("admits a custom role whose only developer menu is feedback and skips the snapshot", async () => {
    listVisibleMenusMock.mockResolvedValue({ items: [menu("/developer/feedback")] });
    getDeveloperSnapshotMock.mockRejectedValue(new Error("HTTP 403"));

    render(<DeveloperWorkspace page="feedback" />);

    expect(await screen.findByText("学生意见反馈")).toBeVisible();
    expect(screen.queryByText(/没有开发者权限/)).not.toBeInTheDocument();
    expect(getDeveloperSnapshotMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "工作台" })).not.toBeInTheDocument();
  });

  it("rejects the shell when no developer menu is visible", async () => {
    listVisibleMenusMock.mockResolvedValue({ items: [] });

    render(<DeveloperWorkspace page="overview" />);

    expect(await screen.findByText(/当前账户没有开发者工作台访问权限/)).toBeVisible();
    expect(getDeveloperSnapshotMock).not.toHaveBeenCalled();
  });

  it("blocks direct navigation to a menu the role does not have", async () => {
    listVisibleMenusMock.mockResolvedValue({ items: [menu("/developer/feedback")] });
    getDeveloperSnapshotMock.mockRejectedValue(new Error("HTTP 403"));

    render(<DeveloperWorkspace page="sessions" />);

    expect(await screen.findByText("无权访问该页面")).toBeVisible();
  });

  it("renders the overview for a fully provisioned developer with snapshot data", async () => {
    listVisibleMenusMock.mockResolvedValue({ items: ALL_ROUTES.map((route) => menu(route)) });
    getDeveloperSnapshotMock.mockResolvedValue(snapshot);

    render(<DeveloperWorkspace />);

    expect(await screen.findByText("后端基础工作台")).toBeVisible();
    expect(await waitFor(() => screen.getByRole("button", { name: "意见反馈" }))).toBeVisible();
    expect(getDeveloperSnapshotMock).toHaveBeenCalledOnce();
  });

  it("keeps data-owned pages usable when the snapshot is denied", async () => {
    listVisibleMenusMock.mockResolvedValue({ items: ALL_ROUTES.map((route) => menu(route)) });
    getDeveloperSnapshotMock.mockRejectedValue(new Error("HTTP 403"));

    render(<DeveloperWorkspace page="release-notes" />);

    expect((await screen.findAllByText("发布说明")).length).toBeGreaterThan(0);
    expect(await waitFor(() => screen.getByRole("button", { name: "工作台" }))).toBeVisible();
    expect(getDeveloperSnapshotMock).toHaveBeenCalledOnce();
    expect(listFeedbackMock).not.toHaveBeenCalled();
  });

  it("does not auto-select or mark the new page row as read", async () => {
    listVisibleMenusMock.mockResolvedValue({ items: [menu("/developer/feedback")] });
    const pageOne = [{
      thread_id: "t1",
      user_id: "u1",
      username: "alice",
      display_name: "Alice",
      unread_count: 1,
      updated_at: "2026-08-27T08:00:00Z",
      status: "open",
      category: "other",
      priority: "medium",
      latest: { id: "m1", sender_type: "student", body: "hi", created_at: "2026-08-27T08:00:00Z" },
    }];
    const pageTwo = [{
      thread_id: "t9",
      user_id: "u9",
      username: "bob",
      display_name: "Bob",
      unread_count: 1,
      updated_at: "2026-08-27T09:00:00Z",
      status: "open",
      category: "feature",
      priority: "medium",
      latest: { id: "m9", sender_type: "student", body: "page two", created_at: "2026-08-27T09:00:00Z" },
    }];
    listFeedbackMock.mockImplementation(async ({ offset } = {}) => ({
      items: offset ? pageTwo : pageOne,
      total: 9,
    }));
    getFeedbackMock.mockResolvedValue({
      thread_id: "t1",
      user_id: "u1",
      username: "alice",
      display_name: "Alice",
      status: "open",
      category: "other",
      priority: "medium",
      messages: [{ id: "m1", sender_type: "student", body: "hi", created_at: "2026-08-27T08:00:00Z" }],
    });

    render(<DeveloperWorkspace page="feedback" />);

    const row = await screen.findByRole("button", { name: /查看.*Alice/ });
    fireEvent.click(row);
    await waitFor(() => expect(markFeedbackReadMock).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: /下一页/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /查看.*Bob/ })).toBeVisible());

    expect(getFeedbackMock).toHaveBeenCalledTimes(1);
    expect(markFeedbackReadMock).toHaveBeenCalledTimes(1);
  });
});
