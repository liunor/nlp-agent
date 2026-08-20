import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api, ensureAuth } from "@/platform/http/api";
import type { AuthSession } from "@/shared/types";

import { AuthProvider } from "@/platform/auth/AuthContext";
import { LoginPage } from "./LoginPage";

vi.mock("@/platform/http/api", () => ({
  ensureAuth: vi.fn(),
  api: { login: vi.fn() },
}));

const session: AuthSession = {
  user_id: "new-user",
  workspace_ids: ["personal-workspace"],
  roles: ["guest"],
  csrf_token: "csrf-1",
  expires_at: 1_900_000_000,
};

describe("LoginPage", () => {
  beforeEach(() => {
    vi.mocked(ensureAuth).mockRejectedValue(new Error("HTTP 401"));
    vi.mocked(api.login).mockReset();
  });

  it("logs in with the database account and returns to the protected destination", async () => {
    vi.mocked(api.login).mockResolvedValue(session);

    render(
      <MemoryRouter initialEntries={[{ pathname: "/login", state: { from: "/developer/users" } }]}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/developer/users" element={<p>用户管理页</p>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "new-user" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "password" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "登录" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    await waitFor(() => expect(screen.getByText("用户管理页")).toBeVisible());
    expect(api.login).toHaveBeenCalledWith("new-user", "password");
  });

  it("does not show a second login form when the session is already valid", async () => {
    vi.mocked(ensureAuth).mockResolvedValue(session);

    render(
      <MemoryRouter initialEntries={[{ pathname: "/login", state: { from: "/developer/users" } }] }>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/developer/users" element={<p>用户管理页</p>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText("用户管理页")).toBeVisible());
    expect(screen.queryByRole("heading", { name: "NLP 学习平台" })).not.toBeInTheDocument();
  });
});
