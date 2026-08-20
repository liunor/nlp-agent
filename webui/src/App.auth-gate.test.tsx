import { render, screen } from "@testing-library/react";

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
    api: { login: vi.fn() },
  };
});

import { App } from "./App";

describe("student authentication gate", () => {
  it("redirects a logged-out browser to the full-page account login", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: "NLP 学习平台" })).toBeVisible();
    expect(screen.getByLabelText("用户名")).toBeVisible();
    expect(screen.queryByLabelText("学习问题")).not.toBeInTheDocument();
  });

  it("does not expose the old student-only login dialog before authentication", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: "NLP 学习平台" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "登录 Nova" })).not.toBeInTheDocument();
  });
});
