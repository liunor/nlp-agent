import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { api } from "@/platform/http/api";
import { LoginDialog } from "@/modules/student/components/LoginDialog";
import { LoginPage } from "./LoginPage";

vi.mock("@/platform/auth/AuthContext", () => ({
  useAuth: () => ({ isAuthenticated: false, isLoading: false, clearAuthNotice: vi.fn() }),
}));
vi.mock("@/platform/http/api", () => ({
  api: { getCaptcha: vi.fn(), sendEmailCode: vi.fn(), register: vi.fn() },
}));

describe.each(["page", "dialog"])("email registration in %s", (surface) => {
  const authenticate = vi.fn();
  const close = vi.fn();

  beforeEach(() => {
    vi.resetAllMocks();
    let captcha = 0;
    vi.mocked(api.getCaptcha).mockImplementation(async () => ({
      captcha_id: `captcha-${++captcha}`, image: "data:image/png;base64,fixture",
    }));
    vi.mocked(api.sendEmailCode).mockResolvedValue({ message: "sent", expires_in: 300, resend_after: 90 });
    vi.mocked(api.register).mockResolvedValue({
      message: "registered", user_id: "fixture", username: "userfixture", display_name: "同学", roles: ["guest"],
    });
  });

  async function openRegistration() {
    render(surface === "page"
      ? <MemoryRouter><LoginPage /></MemoryRouter>
      : <LoginDialog open onClose={close} onAuthenticate={authenticate} />);
    fireEvent.click(screen.getByRole("button", { name: "立即注册" }));
    await screen.findByAltText("验证码");
    expect(screen.queryByText("手机号")).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("请输入邮箱"), { target: { value: "fixture@example.com" } });
    fireEvent.change(screen.getByPlaceholderText("输入图中字符"), { target: { value: "ABCD" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "发送验证码" })); });
  }

  async function completeForm() {
    await screen.findByAltText("注册验证码");
    fireEvent.change(screen.getByPlaceholderText("6位验证码"), { target: { value: "123456" } });
    fireEvent.change(screen.getByPlaceholderText("至少8位"), { target: { value: "fixture-password" } });
    fireEvent.change(screen.getByPlaceholderText("再次输入密码"), { target: { value: "fixture-password" } });
    fireEvent.change(screen.getAllByPlaceholderText("输入图中字符")[1], { target: { value: "EFGH" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "我已阅读并同意用户协议和隐私政策" }));
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "注册" })); });
  }

  it("uses email verification and server expiry/cooldown, then returns to login without auto-login", async () => {
    await openRegistration();
    expect(await screen.findByRole("status")).toHaveTextContent("有效期 5 分钟");
    expect(screen.getByText("90s").closest("button")).toBeDisabled();
    expect(api.sendEmailCode).toHaveBeenCalledWith("fixture@example.com", "captcha-1", "ABCD");
    await completeForm();
    expect(await screen.findByText("注册成功，请使用邮箱登录")).toBeVisible();
    expect(api.register).toHaveBeenCalledWith({
      email: "fixture@example.com", email_code: "123456", password: "fixture-password",
      display_name: undefined, captcha_id: "captcha-2", captcha_code: "EFGH",
    });
    expect(authenticate).not.toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();
  });

  it("shows delivery failure, refreshes the consumed captcha and does not claim the email was sent", async () => {
    vi.mocked(api.sendEmailCode).mockRejectedValue(new Error("邮件发送失败，请稍后重试"));
    await openRegistration();
    expect(await screen.findByText("邮件发送失败，请稍后重试")).toBeVisible();
    await waitFor(() => expect(api.getCaptcha).toHaveBeenCalledTimes(2));
    expect(screen.getByPlaceholderText("6位验证码")).toBeDisabled();
    expect(screen.queryByText(/有效期/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "发送验证码" })).toBeDisabled();
    expect(api.register).not.toHaveBeenCalled();
  });

  it("keeps the email and refreshes registration captcha when the email code is rejected", async () => {
    vi.mocked(api.register).mockRejectedValue(new Error("邮箱验证码错误或已过期"));
    await openRegistration();
    await completeForm();
    expect(await screen.findByText("邮箱验证码错误或已过期")).toBeVisible();
    await waitFor(() => expect(api.getCaptcha).toHaveBeenCalledTimes(3));
    expect(screen.getByPlaceholderText("请输入邮箱")).toHaveValue("fixture@example.com");
    expect(screen.getAllByPlaceholderText("输入图中字符")[1]).toHaveValue("");
    expect(screen.queryByText("注册成功，请使用邮箱登录")).not.toBeInTheDocument();
    expect(authenticate).not.toHaveBeenCalled();
  });
});
