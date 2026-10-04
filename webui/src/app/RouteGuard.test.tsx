import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { useAuth } from "@/platform/auth/AuthContext";

import { RouteGuard } from "./RouteGuard";

vi.mock("@/platform/auth/AuthContext", () => ({
  useAuth: vi.fn(),
}));

describe("RouteGuard", () => {
  it.each([["student"], ["guest"]])("redirects %s away from teacher routes", (role) => {
    vi.mocked(useAuth).mockReturnValue({
      user: { roles: [role], workspace_ids: ["default"] } as never,
      roles: [role],
      isAuthenticated: true,
      isLoading: false,
      isAuthExpired: false,
      login: vi.fn(),
      logout: vi.fn(),
      error: "",
    });

    render(<MemoryRouter initialEntries={["/teacher/exercises"]}>
      <Routes>
        <Route path="/teacher/exercises" element={<RouteGuard allowedRoles={["teacher", "developer", "admin"]}><span>教师页面</span></RouteGuard>} />
        <Route path="/" element={<span>学生首页</span>} />
      </Routes>
    </MemoryRouter>);

    expect(screen.queryByText("教师页面")).not.toBeInTheDocument();
    expect(screen.getByText("学生首页")).toBeInTheDocument();
  });
});
