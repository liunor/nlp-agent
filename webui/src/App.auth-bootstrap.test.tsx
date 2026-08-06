import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

const harness = vi.hoisted(() => {
  let authenticated = false;
  let releaseAuth!: () => void;
  const authGate = new Promise<void>((resolve) => { releaseAuth = resolve; });
  return {
    authenticate: async () => { await authGate; authenticated = true; return {}; },
    releaseAuth,
    getLearningCatalog: vi.fn(async () => {
      if (!authenticated) throw new Error("HTTP 401");
      return {
        catalog: {
          workspace_id: "default",
          topics: [{ id: "transformer", name: "Transformer", description: "", status: "enabled", knowledge_points: [] }],
          exercise_blueprints: [],
          review_blueprints: [],
          guided_blueprints: [],
        },
      };
    }),
  };
});

vi.mock("@/platform/realtime/client", () => ({ StudentSocket: class { connect() {} close() {} setSession() {} sendChat() {} resume() {} cancel() {} } }));
vi.mock("@/platform/http/api", () => ({
  ensureAuth: harness.authenticate,
  api: {
    getMe: vi.fn().mockResolvedValue({ user_id: "u1", username: "test", display_name: "Test", status: "active", roles: ["student"], workspace_ids: ["default"], permissions: [], created_at: "", updated_at: "" }),
    getAuthSession: harness.authenticate,
    listSessions: vi.fn().mockResolvedValue({ items: [] }),
    getSettings: vi.fn().mockResolvedValue({ preferences: { settings: {} }, runtime: { default_model_profile: "deepseek", model_profiles: {} } }),
    getLearningCatalog: harness.getLearningCatalog,
  },
}));

import { App } from "./App";

describe("student authentication bootstrap", () => {
  it("loads the learning catalogue after authentication completes", async () => {
    render(<App />);
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 20)); });
    expect(harness.getLearningCatalog).not.toHaveBeenCalled();
    await act(async () => { harness.releaseAuth(); });

    await screen.findByText("《自然语言处理》智能体 欢迎您！");
    await waitFor(() => expect(harness.getLearningCatalog).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "学习设置" }));
    fireEvent.click(screen.getByRole("button", { name: "学习主题" }));
    expect(await screen.findByRole("option", { name: "Transformer" })).toBeVisible();
  });
});
