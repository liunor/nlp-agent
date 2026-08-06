import { render, screen } from "@testing-library/react";

vi.mock("@/platform/http/api", () => ({
  ensureAuth: vi.fn().mockResolvedValue({
    roles: ["admin", "teacher"],
    workspace_ids: ["default"],
  }),
  api: {
    getMe: vi.fn().mockResolvedValue({ user_id: "teacher", username: "teacher", display_name: "Teacher", status: "active", roles: ["admin", "teacher"], workspace_ids: ["default"], permissions: [], created_at: "", updated_at: "" }),
    getAuthSession: vi.fn().mockResolvedValue({ roles: ["admin", "teacher"], workspace_ids: ["default"] }),
    getSettings: vi.fn().mockResolvedValue({ preferences: { settings: {} }, runtime: { default_model_profile: "deepseek", model_profiles: {} } }),
    getTeacherOverview: vi.fn().mockResolvedValue({
      workspace_id: "default",
      period_days: 30,
      summary: { questions: 0, sessions: 0, students: 0, error_questions: 0 },
      questions: [],
      weak_topics: [],
      frequent_questions: [],
      topic_distribution: [],
      difficulty_distribution: [],
      type_distribution: [],
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
