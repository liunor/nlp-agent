import { render, screen } from "@testing-library/react";

vi.mock("@/platform/http/api", () => ({
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
});
