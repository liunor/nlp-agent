import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AdminRoutes } from "./routes";

const { listUsersMock } = vi.hoisted(() => ({
  listUsersMock: vi.fn(),
}));

vi.mock("@/platform/http/api", () => ({
  api: {
    listUsers: listUsersMock,
  },
}));

vi.mock("@/platform/auth/AuthContext", () => ({
  useAuth: () => ({
    user: { user_id: "admin" },
    roles: ["admin"],
    logout: vi.fn(),
  }),
}));

describe("AdminRoutes", () => {
  beforeEach(() => {
    listUsersMock.mockReset().mockResolvedValue({ users: [], total: 0, offset: 0, limit: 1 });
  });

  it("keeps the removed workspace and classroom entries out of the admin navigation", async () => {
    render(
      <MemoryRouter initialEntries={["/admin"]}>
        <Routes>
          <Route path="/admin/*" element={<AdminRoutes />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "系统管理概览" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "工作区" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "班级管理" })).not.toBeInTheDocument();
    expect(listUsersMock).toHaveBeenCalledWith(0, 1);
  });

  it.each(["/admin/workspaces", "/admin/classrooms"])(
    "does not render the removed page at %s",
    async (path) => {
      render(
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/admin/*" element={<AdminRoutes />} />
          </Routes>
        </MemoryRouter>,
      );

      expect(await screen.findByRole("heading", { name: "页面未找到" })).toBeVisible();
    },
  );
});
