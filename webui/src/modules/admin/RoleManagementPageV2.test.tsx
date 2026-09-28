import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { RoleManagementPageV2 } from "./RoleManagementPageV2";

const { listPermissionsMock, listRolePermissionsMock, listRolesMock, replaceRolePermissionsMock } = vi.hoisted(() => ({
  listPermissionsMock: vi.fn(),
  listRolePermissionsMock: vi.fn(),
  listRolesMock: vi.fn(),
  replaceRolePermissionsMock: vi.fn(),
}));

vi.mock("@/platform/http/api", () => ({
  api: {
    listPermissions: listPermissionsMock,
    listRolePermissions: listRolePermissionsMock,
    listRoles: listRolesMock,
    replaceRolePermissions: replaceRolePermissionsMock,
  },
}));

describe("RoleManagementPageV2", () => {
  beforeEach(() => {
    listRolesMock.mockReset().mockResolvedValue({
      items: [
        { code: "student", name: "学生", description: "学习用户", status: "active", is_builtin: true },
        { code: "developer", name: "开发者", description: "平台管理用户", status: "active", is_builtin: true },
      ],
    });
    listPermissionsMock.mockReset().mockResolvedValue({
      items: [
        { code: "agent:session:read", name: "查看智能体会话", description: "查看可访问会话", status: "active" },
        { code: "learning:content:read_public", name: "查看公开学习内容", description: "查看公开课程内容", status: "active" },
      ],
    });
    listRolePermissionsMock.mockReset().mockResolvedValue({
      role_code: "student",
      permissions: { "agent:session:read": ["own"] },
    });
    replaceRolePermissionsMock.mockReset().mockResolvedValue(undefined);
  });

  it("opens with the first role selected and loads its permission grants", async () => {
    render(<RoleManagementPageV2 />);

    expect(await screen.findByRole("heading", { name: "学生" })).toBeVisible();
    expect(screen.getByText("查看智能体会话")).toBeVisible();
    expect(screen.queryByText("选择角色查看权限")).not.toBeInTheDocument();
    expect(screen.queryByText("ACCESS CONTROL")).not.toBeInTheDocument();
    expect(screen.queryByText("ROLE CATALOG")).not.toBeInTheDocument();
    await waitFor(() => expect(listRolePermissionsMock).toHaveBeenCalledWith("student"));
  });

  it("saves selected permissions with a scope payload and refreshes the shell", async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    render(<RoleManagementPageV2 onShellRefresh={refresh} />);

    await screen.findByRole("heading", { name: "学生" });
    fireEvent.click(screen.getByRole("checkbox", { name: "授权 查看公开学习内容" }));
    fireEvent.click(screen.getByRole("button", { name: "保存权限" }));

    await waitFor(() => expect(replaceRolePermissionsMock).toHaveBeenCalledWith(
      "student",
      ["agent:session:read", "learning:content:read_public"],
      { "agent:session:read": ["own"], "learning:content:read_public": ["public"] },
    ));
    expect(refresh).toHaveBeenCalledOnce();
    expect(screen.getByText("权限已保存，受影响用户的授权版本已更新")).toBeVisible();
  });

  it("does not report a successful permission save as a save failure when shell refresh fails", async () => {
    const refresh = vi.fn().mockRejectedValue(new Error("shell refresh failed"));
    render(<RoleManagementPageV2 onShellRefresh={refresh} />);

    await screen.findByRole("heading", { name: "学生" });
    fireEvent.click(screen.getByRole("checkbox", { name: "授权 查看公开学习内容" }));
    fireEvent.click(screen.getByRole("button", { name: "保存权限" }));

    await waitFor(() => expect(replaceRolePermissionsMock).toHaveBeenCalled());
    expect(screen.getByText("权限已保存，但页面刷新失败，请手动刷新")).toBeVisible();
    expect(screen.queryByText("保存权限失败")).not.toBeInTheDocument();
  });
});
