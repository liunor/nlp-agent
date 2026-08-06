import { fireEvent, render, screen } from "@testing-library/react";

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
      login: vi.fn() 
    },
  };
});

import { App } from "./App";

describe("student authentication gate", () => {
  it("keeps the normal full-width student shell and header while logged out", async () => {
    const { container } = render(<App />);

    await screen.findByLabelText("学习问题");

    expect(container.querySelector(".unauthenticated-student-shell")).toHaveClass("thread-shell");
    expect(container.querySelector(".unauthenticated-brand")).not.toBeInTheDocument();
    expect(container.querySelector(".thread-header .school-logo")).toBeInTheDocument();
  });

  it("opens the reusable login dialog when an unauthenticated student sends a question", async () => {
    render(<App />);

    const question = await screen.findByLabelText("学习问题");
    fireEvent.change(question, { target: { value: "什么是 TF-IDF？" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByRole("heading", { name: "登录 Nova" })).toBeVisible();
  });
});
