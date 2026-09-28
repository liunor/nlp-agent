import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api } from "@/platform/http/api";
import { LoginDialog } from "./LoginDialog";

vi.mock("@/platform/http/api", () => ({
  api: {
    getCaptcha: vi.fn(),
    sendEmailCode: vi.fn(),
    register: vi.fn(),
  },
}));

describe("LoginDialog", () => {
  beforeEach(() => {
    vi.mocked(api.getCaptcha).mockReset();
    vi.mocked(api.getCaptcha).mockResolvedValue({ captcha_id: "captcha-1", image: "data:image/png;base64,abc" });
    vi.mocked(api.sendEmailCode).mockReset();
  });
  it("renders without loading a decorative logo image", () => {
    render(<LoginDialog open onClose={vi.fn()} onAuthenticate={vi.fn()} />);

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("submits fixed credentials and closes only after successful verification", async () => {
    const authenticate = vi.fn().mockResolvedValue(undefined);
    const close = vi.fn();
    render(<LoginDialog open onClose={close} onAuthenticate={authenticate} />);

    fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: "nova" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "test-password" } });
    fireEvent.click(screen.getByRole("button", { name: "登录并继续" }));

    await waitFor(() => expect(authenticate).toHaveBeenCalledWith("nova", "test-password"));
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("keeps the dialog open and shows the server error when verification fails", async () => {
    const authenticate = vi.fn().mockRejectedValue(new Error("账号或密码错误"));
    const close = vi.fn();
    render(<LoginDialog open onClose={close} onAuthenticate={authenticate} />);

    fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: "nova" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "登录并继续" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("账号或密码错误");
    expect(close).not.toHaveBeenCalled();
  });

  it("renders the registration captcha at a readable fixed size", async () => {
    render(<LoginDialog open onClose={vi.fn()} onAuthenticate={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "立即注册" }));

    const captcha = await screen.findByAltText("验证码");
    expect(captcha).toHaveStyle({ width: "160px", height: "64px" });
  });

  it("locks email while sending and refreshes consumed captcha after an email change", async () => {
    vi.mocked(api.getCaptcha)
      .mockReset()
      .mockResolvedValueOnce({ captcha_id: "email-1", image: "data:image/png;base64,email1" })
      .mockResolvedValueOnce({ captcha_id: "reg-1", image: "data:image/png;base64,reg1" })
      .mockResolvedValueOnce({ captcha_id: "email-2", image: "data:image/png;base64,email2" });
    let finishSending!: () => void;
    vi.mocked(api.sendEmailCode).mockImplementation(() => new Promise((resolve) => {
      finishSending = () => resolve({ message: "sent" });
    }));
    render(<LoginDialog open onClose={vi.fn()} onAuthenticate={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "立即注册" }));

    const email = screen.getByPlaceholderText("请输入邮箱");
    fireEvent.change(email, { target: { value: "first@example.com" } });
    fireEvent.change(await screen.findByPlaceholderText("输入图中字符"), { target: { value: "ABCD" } });
    fireEvent.click(screen.getByRole("button", { name: "发送验证码" }));
    expect(email).toBeDisabled();
    finishSending();
    await waitFor(() => expect(screen.getByPlaceholderText("6位验证码")).toBeEnabled());

    fireEvent.change(email, { target: { value: "second@example.com" } });
    expect(screen.getByPlaceholderText("6位验证码")).toBeDisabled();
    await waitFor(() => expect(api.getCaptcha).toHaveBeenCalledTimes(3));
    expect(screen.getByAltText("验证码")).toHaveAttribute("src", "data:image/png;base64,email2");
  });
});
it("shows a session-expired message when reopened after authentication expires", () => {
  render(
    <LoginDialog
      open
      expired
      onClose={vi.fn()}
      onAuthenticate={vi.fn()}
    />,
  );

  expect(screen.getByRole("alert")).toHaveTextContent(
    "登录状态已失效，请重新登录后继续使用。",
  );
  });
  it("prevents dismissing the dialog while the session is expired", () => {
  const close = vi.fn();

  render(
    <LoginDialog
      open
      expired
      onClose={close}
      onAuthenticate={vi.fn()}
    />,
  );

  expect(screen.queryByRole("button", { name: "关闭" })).not.toBeInTheDocument();

  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });

  expect(close).not.toHaveBeenCalled();
});
it("keeps the expired dialog open when re-login credentials are wrong", async () => {
  const authenticate = vi.fn().mockRejectedValue(new Error("账号或密码错误"));
  const close = vi.fn();

  render(
    <LoginDialog
      open
      expired
      onClose={close}
      onAuthenticate={authenticate}
    />,
  );

  fireEvent.change(screen.getByLabelText("邮箱"), {
    target: { value: "nova" },
  });

  fireEvent.change(screen.getByLabelText("密码"), {
    target: { value: "wrong-password" },
  });

  fireEvent.click(
    screen.getByRole("button", { name: "登录并继续" }),
  );

  await waitFor(() => {
    expect(authenticate).toHaveBeenCalledWith("nova", "wrong-password");
  });

  expect(await screen.findByText("账号或密码错误")).toBeVisible();
  expect(close).not.toHaveBeenCalled();

  expect(
    screen.getByText("登录状态已失效，请重新登录后继续使用。"),
  ).toBeVisible();
});
