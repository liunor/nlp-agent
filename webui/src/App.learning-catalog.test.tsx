import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({
  listSessions: vi.fn().mockResolvedValue({ items: [] }),
  getSettings: vi.fn().mockResolvedValue({ preferences: { settings: {} }, runtime: { default_model_profile: "deepseek", model_profiles: {} } }),
  getLearningCatalog: vi.fn()
    .mockResolvedValueOnce({ catalog: { topics: [{ id: "transformer", name: "Transformer", description: "", status: "enabled", knowledge_points: [] }] } })
    .mockResolvedValueOnce({ catalog: { topics: [] } }),
}));

vi.mock("@/platform/realtime/client", () => ({ StudentSocket: class { connect() {} close() {} setSession() {} sendChat() {} resume() {} cancel() {} } }));
vi.mock("@/platform/http/api", () => ({
  AUTH_EXPIRED_EVENT: "nova:auth-expired",
  ensureAuth: vi.fn().mockResolvedValue({}),
  api,
}));

import { App } from "./App";

describe("student learning catalogue refresh", () => {
  it("clears a selected topic when a foreground refresh shows it is no longer available", async () => {
    localStorage.setItem("nlp-agent.learning-preferences.v1", JSON.stringify({
      version: 2,
      context: { topic_id: "transformer", topic_name: "Transformer", level: "beginner", mode: "explain" },
      sessions: {}, categories: [],
    }));

    render(<App />);
    await waitFor(() => expect(api.getLearningCatalog).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole("button", { name: "学习设置" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "学习主题" })).toHaveTextContent("Transformer"));

    act(() => window.dispatchEvent(new Event("focus")));

    await waitFor(() => expect(api.getLearningCatalog.mock.calls.length).toBeGreaterThanOrEqual(2));
    await waitFor(() => expect(screen.getByRole("button", { name: "学习主题" })).toHaveTextContent("未选择主题"));
  });

  it("shows a dismissible in-page configuration notice instead of a sidebar error", async () => {
    localStorage.setItem("nlp-agent.learning-preferences.v1", JSON.stringify({
      version: 2,
      context: { topic_id: "transformer", topic_name: "Transformer", level: "beginner", mode: "explain" },
      sessions: {}, categories: [],
    }));
    api.getLearningCatalog.mockResolvedValue({ catalog: { topics: [{ id: "transformer", name: "Transformer", description: "", status: "enabled", knowledge_points: [] }], exercise_blueprints: [], review_blueprints: [] } });

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "学习设置" }));
    fireEvent.click(screen.getByRole("button", { name: "教学模式" }));
    fireEvent.click(screen.getByRole("option", { name: "练习模式（未配置）" }));

    expect(screen.getByRole("alert")).toHaveTextContent("练习模式尚未配置蓝图");
    expect(screen.getByRole("button", { name: "去配置" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "关闭提示" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
