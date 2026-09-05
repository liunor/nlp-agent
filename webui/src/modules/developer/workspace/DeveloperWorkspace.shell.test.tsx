import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { DeveloperWorkspace } from "./DeveloperWorkspace";

const { ensureAuthMock, listVisibleMenusMock, getDeveloperSnapshotMock, listFeedbackMock } = vi.hoisted(() => ({
  ensureAuthMock: vi.fn(),
  listVisibleMenusMock: vi.fn(),
  getDeveloperSnapshotMock: vi.fn(),
  listFeedbackMock: vi.fn(),
}));
vi.mock("@/platform/http/api", () => ({
  api: {
    listVisibleMenus: listVisibleMenusMock,
    getDeveloperSnapshot: getDeveloperSnapshotMock,
    listFeedback: listFeedbackMock,
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

const ALL_ROUTES = ["/developer", "/developer/agents", "/developer/tools", "/developer/models", "/developer/mcp", "/developer/skills", "/developer/release-notes", "/developer/automations", "/developer/feedback", "/developer/settings", "/developer/users", "/developer/roles", "/developer/menus"];

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
    ensureAuthMock.mockResolvedValue({ roles: ["custom"], permissions: ["learning:feedback:read"] });
    listFeedbackMock.mockResolvedValue({ items: [], total: 0 });
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

  it("does not expose Agent session management even when a stale menu row remains", async () => {
    listVisibleMenusMock.mockResolvedValue({ items: [...ALL_ROUTES.map((route) => menu(route)), menu("/developer/sessions")] });
    getDeveloperSnapshotMock.mockResolvedValue(snapshot);

    render(<DeveloperWorkspace />);

    expect(await screen.findByText("后端基础工作台")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Agent 会话" })).not.toBeInTheDocument();
  });

  it("renders the overview for a fully provisioned developer with snapshot data", async () => {
    listVisibleMenusMock.mockResolvedValue({ items: ALL_ROUTES.map((route) => menu(route)) });
    getDeveloperSnapshotMock.mockResolvedValue(snapshot);

    render(<DeveloperWorkspace />);

    expect(await screen.findByText("后端基础工作台")).toBeVisible();
    expect(await waitFor(() => screen.getByRole("button", { name: "意见反馈" }))).toBeVisible();
    expect(getDeveloperSnapshotMock).toHaveBeenCalledOnce();
  });

  it("keeps authorization audit out of the developer control-plane navigation", async () => {
    // A stale server-side menu row must not resurrect audit inside the
    // developer shell while the migration is rolling out.
    listVisibleMenusMock.mockResolvedValue({ items: [...ALL_ROUTES.map((route) => menu(route)), menu("/developer/audit")] });
    getDeveloperSnapshotMock.mockResolvedValue(snapshot);

    render(<DeveloperWorkspace />);

    expect(await screen.findByText("后端基础工作台")).toBeVisible();
    expect(screen.queryByRole("button", { name: "审计日志" })).not.toBeInTheDocument();
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

  it("clears the previous runtime snapshot when a refresh loses inspect permission", async () => {
    listVisibleMenusMock.mockResolvedValue({ items: ALL_ROUTES.map((route) => menu(route)) });
    getDeveloperSnapshotMock
      .mockResolvedValueOnce(snapshot)
      .mockRejectedValueOnce(new Error("HTTP 403"));

    render(<DeveloperWorkspace page="agents" />);

    expect(await screen.findByRole("heading", { name: "Agent 与 Worker" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "刷新数据" }));

    await waitFor(() => expect(getDeveloperSnapshotMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("无法读取运行时快照")).toBeVisible();
  });
});
