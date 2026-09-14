import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api, ensureAuth } from "@/platform/http/api";
import type { AuthSession } from "@/shared/types";

import { AuthProvider } from "@/platform/auth/AuthContext";
import { LoginPage } from "./LoginPage";

vi.mock("@/platform/http/api", () => ({
  AUTH_EXPIRED_EVENT: "nova:auth-expired",
  ensureAuth: vi.fn(),
  api: { login: vi.fn(), getCaptcha: vi.fn() },
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
    vi.mocked(api.getCaptcha).mockReset();
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

  it("renders a readable-sized CAPTCHA after switching to registration", async () => {
    vi.mocked(api.getCaptcha).mockResolvedValue({
      captcha_id: "captcha-1",
      image: "data:image/png;base64,captcha",
    });

    render(
      <MemoryRouter initialEntries={["/login"]}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "立即注册" }));

    const captcha = await screen.findByRole("img", { name: "验证码" });
    expect(captcha).toHaveClass("w-32", "h-12");
  });
});
