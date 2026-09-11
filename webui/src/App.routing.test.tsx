import { act, render, screen } from "@testing-library/react";

vi.mock("@/platform/http/api", () => ({
  AUTH_EXPIRED_EVENT: "nova:auth-expired",
  ensureAuth: vi.fn().mockResolvedValue({
    roles: ["admin", "teacher"],
    workspace_ids: ["default"],
  }),
  api: {
    getSettings: vi.fn().mockResolvedValue({ preferences: { settings: {} }, runtime: { default_model_profile: "deepseek", model_profiles: {} } }),
    getTeacherOverview: vi.fn().mockResolvedValue({
      workspace_id: "default",
      period_days: 30,
      summary: { questions: 0, sessions: 0, students: 0, error_questions: 0, exercises: 0, exercise_pass_rate: 0, guided_sessions: 0 },
      weak_topics: [],
      topic_distribution: [],
      difficulty_distribution: [],
      mode_distribution: [],
      knowledge_point_stats: [],
    }),
    getTeacherCatalog: vi.fn().mockResolvedValue({
      catalog: {
        workspace_id: "default",
        topics: [],
        exercise_blueprints: [],
        review_blueprints: [],
        guided_blueprints: [],
      },
    }),
  },
}));

import { App } from "./App";

describe("application routing", () => {
  it("shows a not-found page for an unknown browser URL", async () => {
    history.pushState({}, "", "/missing-page");

    render(<App />);

    expect(await screen.findByRole("heading", { name: "页面未找到" })).toBeVisible();
    expect(screen.getByRole("link", { name: "返回学生空间" })).toHaveAttribute("href", "/");
  });

  it("rejects unknown nested teacher URLs instead of silently opening the overview", async () => {
    history.pushState({}, "", "/teacher/missing-page");

    render(<App />);

    expect(await screen.findByRole("heading", { name: "页面未找到" })).toBeVisible();
  });
  it.each([
  "/teacher/missing-page",
  "/developer/missing-page",
])("shows the global expired-login dialog on protected route %s", async (path) => {
  history.pushState({}, "", path);

  render(<App />);

  await screen.findByRole("heading");

  act(() => {
    window.dispatchEvent(new Event("nova:auth-expired"));
  });

  expect(
    await screen.findByText("登录状态已失效，请重新登录后继续使用。"),
  ).toBeVisible();
});
});
