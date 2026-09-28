import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api, ensureAuth } from "@/platform/http/api";
import type { AuthSession } from "@/shared/types";

import { AuthProvider } from "@/platform/auth/AuthContext";
import { LoginPage } from "./LoginPage";

vi.mock("@/platform/http/api", () => ({
  AUTH_EXPIRED_EVENT: "nova:auth-expired",
  ensureAuth: vi.fn(),
  api: {
    login: vi.fn(),
    getCaptcha: vi.fn(),
    sendEmailCode: vi.fn(),
    register: vi.fn(),
  },
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
    vi.mocked(api.getCaptcha).mockResolvedValue({ captcha_id: "captcha-1", image: "data:image/png;base64,abc" });
    vi.mocked(api.sendEmailCode).mockReset();
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

    fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: "new-user" } });
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

  it("shows readable registration captchas and resets sent state when email changes", async () => {
    vi.mocked(api.getCaptcha)
      .mockReset()
      .mockResolvedValueOnce({ captcha_id: "email-1", image: "data:image/png;base64,email1" })
      .mockResolvedValueOnce({ captcha_id: "reg-1", image: "data:image/png;base64,reg1" })
      .mockResolvedValueOnce({ captcha_id: "email-2", image: "data:image/png;base64,email2" });
    let finishSending!: () => void;
    vi.mocked(api.sendEmailCode).mockImplementation(() => new Promise((resolve) => {
      finishSending = () => resolve({ message: "sent" });
    }));
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <AuthProvider><LoginPage /></AuthProvider>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "立即注册" }));
    const email = screen.getByLabelText("邮箱");
    fireEvent.change(email, { target: { value: "first@example.com" } });
    fireEvent.change(await screen.findByPlaceholderText("输入图中字符"), { target: { value: "ABCD" } });

    const captcha = await screen.findByAltText("验证码");
    expect(captcha).toHaveClass("h-16", "w-40");
    fireEvent.click(screen.getByRole("button", { name: "发送验证码" }));
    expect(email).toBeDisabled();
    finishSending();
    await waitFor(() => expect(screen.getByPlaceholderText("6位验证码")).toBeEnabled());

    fireEvent.change(email, { target: { value: "second@example.com" } });
    expect(screen.getByPlaceholderText("6位验证码")).toBeDisabled();
    expect(screen.queryByText("注册验证")).not.toBeInTheDocument();
    await waitFor(() => expect(api.getCaptcha).toHaveBeenCalledTimes(3));
    expect(screen.getByAltText("验证码")).toHaveAttribute("src", "data:image/png;base64,email2");
  });
});
