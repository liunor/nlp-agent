import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { UserManagementPage } from "./UserManagementPage";

const {
  createUserMock,
  getUserRolesMock,
  listRolesMock,
  listUsersMock,
} = vi.hoisted(() => ({
  createUserMock: vi.fn(),
  getUserRolesMock: vi.fn(),
  listRolesMock: vi.fn(),
  listUsersMock: vi.fn(),
}));

vi.mock("@/platform/http/api", () => ({
  api: {
    createUser: createUserMock,
    getUserRoles: getUserRolesMock,
    listRoles: listRolesMock,
    listUsers: listUsersMock,
  },
}));

const alice = {
  id: "user-alice",
  username: "alice",
  display_name: "Alice",
  status: "active" as const,
  created_at: "2026-09-03T08:00:00Z",
  updated_at: "2026-09-03T08:00:00Z",
  deleted_at: null,
  last_login_at: "2026-09-03T09:00:00Z",
  roles: ["student"],
};

const bob = {
  ...alice,
  id: "user-bob",
  username: "bob",
  display_name: "Bob",
  roles: ["teacher"],
};

function pageFor(offset: number, limit: number) {
  return {
    users: offset === 0 ? [alice] : [bob],
    total: 20,
    offset,
    limit,
  };
}

describe("UserManagementPage", () => {
  beforeEach(() => {
    vi.useRealTimers();
    vi.stubGlobal("confirm", vi.fn(() => true));
    createUserMock.mockReset();
    getUserRolesMock.mockReset().mockResolvedValue({ user_id: alice.id, role_codes: ["student"] });
    listRolesMock.mockReset().mockResolvedValue({ items: [
      { code: "student", name: "学生", description: "学习者", status: "active", is_builtin: true },
      { code: "teacher", name: "教师", description: "教师", status: "active", is_builtin: true },
    ] });
    listUsersMock.mockReset().mockImplementation(async (offset = 0, limit = 10) => pageFor(offset, limit));
  });

  it("keeps a compact create form and puts search inside the user list surface", async () => {
    render(<UserManagementPage />);

    expect(await screen.findByText("alice")).toBeVisible();
    expect(screen.getByRole("heading", { name: "创建用户" })).toBeVisible();
    expect(screen.getByLabelText("用户名")).toBeVisible();
    expect(screen.queryByText("点击展开创建账号")).not.toBeInTheDocument();

    const listRegion = screen.getByRole("region", { name: "用户列表" });
    expect(within(listRegion).getByLabelText("搜索用户名或显示名称")).toBeVisible();
  });

  it("uses an inline role trigger and a labelled action menu for each row", async () => {
    render(<UserManagementPage />);

    await screen.findByText("alice");
    expect(within(screen.getByRole("region", { name: "用户列表" })).getByText("学生")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "alice 操作" }));

    expect(screen.getByRole("menu", { name: "alice 操作菜单" })).toBeVisible();
    expect(screen.getByRole("menuitem", { name: "编辑显示名" })).toBeVisible();
    expect(screen.getByRole("button", { name: "alice 角色" })).toBeVisible();
  });

  it("opens role management in a dialog instead of expanding the page", async () => {
    render(<UserManagementPage />);

    await screen.findByText("alice");
    fireEvent.click(screen.getByRole("button", { name: "alice 角色" }));

    const dialog = await screen.findByRole("dialog", { name: "alice 的角色" });
    expect(dialog).toBeVisible();
    expect(within(dialog).getByRole("checkbox", { name: "学生" })).toBeChecked();
  });

  it("loads one page at a time and reuses a cached page when navigating back", async () => {
    render(<UserManagementPage />);

    await screen.findByText("alice");
    expect(listUsersMock).toHaveBeenCalledTimes(1);
    expect(listUsersMock).toHaveBeenCalledWith(0, 10, undefined, undefined, false);

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(screen.getByText("bob")).toBeVisible());
    expect(listUsersMock).toHaveBeenCalledWith(10, 10, undefined, undefined, false);

    const callsBeforeBack = listUsersMock.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "第 1 页" }));
    await waitFor(() => expect(screen.getByText("alice")).toBeVisible());
    expect(listUsersMock.mock.calls.length).toBe(callsBeforeBack);
  });

  it("debounces keyword search without leaving the list page", async () => {
    render(<UserManagementPage />);

    await screen.findByText("alice");
    fireEvent.change(screen.getByLabelText("搜索用户名或显示名称"), { target: { value: "alice" } });

    await waitFor(() => expect(listUsersMock).toHaveBeenCalledWith(0, 10, undefined, "alice", false), { timeout: 2_000 });
    expect(screen.getByRole("region", { name: "用户列表" })).toBeVisible();
  });
});
