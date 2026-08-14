import { fireEvent, render, screen } from "@testing-library/react";

import { api } from "@/platform/http/api";

vi.mock("@/platform/realtime/client", () => ({ StudentSocket: class { connect() {} close() {} setSession() {} sendChat() {} resume() {} cancel() {} } }));
vi.mock("@/platform/http/api", () => {
  class ApiError extends Error {
    constructor(message: string, public readonly status: number) {
      super(message);
    }
  }
  return {
    ApiError,
    ensureAuth: vi.fn().mockRejectedValue(new ApiError("Authentication required", 401)),
    api: {
      getMe: vi.fn().mockRejectedValue(new ApiError("Authentication required", 401)),
      getAuthSession: vi.fn().mockRejectedValue(new ApiError("Authentication required", 401)),
      login: vi.fn(),
    },
  };
});

import { App } from "./App";

describe("student authentication gate", () => {
  it("redirects unauthenticated users to the login page (design §7 认证优先)", async () => {
    render(<App />);

    // 未登录 → AuthGate 跳转到 /login，渲染登录页
    expect(await screen.findByText("请登录以继续")).toBeInTheDocument();
    expect(screen.getByLabelText("用户名")).toBeInTheDocument();
    expect(screen.getByLabelText("密码")).toBeInTheDocument();

    // 受保护的会话输入（“学习问题”）被门控挡在 /login 之外，不应出现
    expect(screen.queryByLabelText("学习问题")).not.toBeInTheDocument();
  });

  it("submits credentials through the login form (design §7 登录闭环)", async () => {
    render(<App />);

    const username = await screen.findByLabelText("用户名");
    const password = screen.getByLabelText("密码");
    fireEvent.change(username, { target: { value: "nova" } });
    fireEvent.change(password, { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    // 登录动作确实被调用，触发认证闭环
    await vi.waitFor(() => expect(api.login).toHaveBeenCalledWith("nova", "secret"));
  });
});
