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
      login: vi.fn(),
      listSessions: vi.fn().mockResolvedValue({ items: [] }),
      getSettings: vi.fn().mockResolvedValue({ preferences: { settings: {} }, runtime: { default_model_profile: "deepseek", model_profiles: {} } }),
      getLearningCatalog: vi.fn().mockResolvedValue({ catalog: { topics: [] } }),
    },
  };
});

import { App } from "./App";

describe("student authentication gate", () => {
  it("shows the student home before authentication", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: "《自然语言处理》智能体 欢迎您！" })).toBeVisible();
    expect(screen.getByLabelText("学习问题")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "NLP 学习平台" })).not.toBeInTheDocument();
  });

  it("opens the login dialog when a guest starts a protected chat", async () => {
    render(<App />);

    const input = await screen.findByLabelText("学习问题");
    await screen.findByRole("heading", { name: "《自然语言处理》智能体 欢迎您！" });
    fireEvent.change(input, { target: { value: "解释 Attention" } });
    screen.getByRole("button", { name: "发送" }).click();

    expect(await screen.findByRole("heading", { name: "登录 Nova" })).toBeVisible();
  });

  it("moves the guest shell into the authenticated workspace after dialog login", async () => {
    vi.mocked(api.login).mockResolvedValue({
      user_id: "user-1",
      workspace_ids: ["default"],
      roles: ["guest"],
      csrf_token: "csrf-1",
      expires_at: 1_900_000_000,
    });
    render(<App />);

    const input = await screen.findByLabelText("学习问题");
    fireEvent.change(input, { target: { value: "解释 Attention" } });
    screen.getByRole("button", { name: "发送" }).click();
    await screen.findByRole("heading", { name: "登录 Nova" });

    fireEvent.change(screen.getByLabelText("账号"), { target: { value: "user-1" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "password" } });
    screen.getByRole("button", { name: "登录并继续" }).click();

    expect(await screen.findByRole("button", { name: "切换主题" })).toBeVisible();
  });
});
