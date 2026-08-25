import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { GuidedBlueprintCatalogEditor } from "./TeacherCatalogManager";
import { TeacherWorkspace } from "./TeacherWorkspace";
import { TeacherRoutes } from "../routes";

const { ensureAuthMock, getSettingsMock, getTeacherCatalog, updateTeacherCatalog } = vi.hoisted(() => ({
  ensureAuthMock: vi.fn(),
  getSettingsMock: vi.fn(),
  getTeacherCatalog: vi.fn().mockResolvedValue({ catalog: { workspace_id: "default", topics: [{ id: "transformer", name: "Transformer", description: "", status: "enabled", knowledge_points: [{ id: "attention", name: "注意力", markdown: "# Attention", status: "enabled", sort_order: 0 }] }], exercise_blueprints: [], review_blueprints: [], guided_blueprints: [] } }),
  updateTeacherCatalog: vi.fn().mockImplementation(async (_workspaceId, next) => ({ catalog: { workspace_id: "default", ...next } })),
}));
vi.mock("@/platform/http/api", () => ({
  ensureAuth: ensureAuthMock,
  api: { getSettings: getSettingsMock, getTeacherOverview: vi.fn().mockResolvedValue({ workspace_id: "default", period_days: 30, summary: { questions: 2, sessions: 2, students: 2, error_questions: 0, exercises: 3, exercise_pass_rate: 66.67, guided_sessions: 1 }, weak_topics: [{ topic_id: "transformer", topic: "Transformer", questions: 2, errors: 0, exercises: 3, average_score: 70, pass_rate: 66.67, misconceptions: 1, risk: "medium" }], topic_distribution: [{ name: "Transformer", count: 2, percentage: 100 }], difficulty_distribution: [{ name: "入门", count: 2, percentage: 100 }], mode_distribution: [{ name: "讲解", count: 2, percentage: 100 }], daily_questions: [{ date: "2026-07-19", count: 2 }], knowledge_point_stats: [{ knowledge_point_id: "attention", name: "注意力", topic: "Transformer", exercises: 3, average_score: 70, pass_rate: 66.67, weak_criteria: [{ criterion: "概念准确", hit_rate: 100 }, { criterion: "步骤完整", hit_rate: 33.33 }] }] }), getTeacherCatalog, updateTeacherCatalog },
}));

describe("TeacherWorkspace catalog CRUD", () => {
  beforeEach(() => {
    updateTeacherCatalog.mockClear();
    getTeacherCatalog.mockClear();
    ensureAuthMock.mockResolvedValue({ roles: ["teacher"], workspace_ids: ["default"] });
    getSettingsMock.mockResolvedValue({ preferences: { settings: {} }, runtime: { default_model_profile: "deepseek", model_profiles: {} } });
  });

  it("loads and saves the teacher catalog in the authorized default workspace", async () => {
    ensureAuthMock.mockResolvedValue({ roles: ["teacher"], workspace_ids: ["default", "research"] });
    getSettingsMock.mockResolvedValue({ preferences: { settings: { default_workspace_id: "research" } }, runtime: { default_model_profile: "deepseek", model_profiles: {} } });
    history.replaceState({}, "", "/teacher/topics");
    render(<TeacherWorkspace />);

    expect(await screen.findByText("research workspace · 目录修改需保存后生效")).toBeVisible();
    expect(getTeacherCatalog).toHaveBeenCalledWith("research");
    fireEvent.click(screen.getByRole("button", { name: "保存教学目录" }));
    await waitFor(() => expect(updateTeacherCatalog).toHaveBeenCalledWith("research", expect.any(Object)));
  });

  it("shows the school logo in the teacher top bar", async () => {
    history.replaceState({}, "", "/teacher"); render(<TeacherWorkspace />);

    expect(await screen.findByAltText("学校校徽")).toBeVisible();
    expect(screen.getByRole("button", { name: "刷新" }).closest(".teacher-brand")).toBeVisible();
  });

  it("updates the visible page when the routed page changes", async () => {
    const view = render(<TeacherWorkspace page="topics" />);

    expect(await screen.findByRole("heading", { name: "主题与知识点" })).toBeVisible();
    view.rerender(<TeacherWorkspace page="questions" />);

    expect(await screen.findByRole("heading", { name: "学生问题" })).toBeVisible();
  });

  it("creates a topic immediately and persists the catalog through FastAPI", async () => {
    history.replaceState({}, "", "/teacher/topics"); render(<TeacherWorkspace />);
    fireEvent.change(await screen.findByPlaceholderText("例如：Transformer"), { target: { value: "词向量" } });
    fireEvent.click(screen.getByRole("button", { name: "创建主题" }));
    expect(screen.queryByDisplayValue("词向量")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "词向量 展开" }));
    expect(screen.getByDisplayValue("词向量")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "保存教学目录" }));
    await waitFor(() => expect(updateTeacherCatalog).toHaveBeenCalledWith("default", expect.objectContaining({ topics: expect.arrayContaining([expect.objectContaining({ name: "词向量" })]) })));
    expect(screen.getByRole("status")).toHaveTextContent("已保存并同步到后端。");
  });

  it("toggles a topic from its full header control without a separate arrow button", async () => {
    history.replaceState({}, "", "/teacher/topics"); render(<TeacherWorkspace />);

    const topicToggle = await screen.findByRole("button", { name: "Transformer 展开" });
    expect(topicToggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: "Transformer 收起" })).not.toBeInTheDocument();

    fireEvent.click(topicToggle);
    expect(screen.getByRole("button", { name: "Transformer 收起" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByDisplayValue("Transformer")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Transformer 收起" }));
    expect(screen.queryByDisplayValue("Transformer")).not.toBeInTheDocument();
  });

  it("edits, disables, creates and deletes knowledge points", async () => {
    history.replaceState({}, "", "/teacher/topics"); render(<TeacherWorkspace />);
    expect(await screen.findByRole("button", { name: "Transformer 展开" })).toBeVisible();
    expect(screen.queryByDisplayValue("Transformer")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Transformer 展开" }));
    expect(screen.getByRole("button", { name: "注意力 展开" })).toBeVisible();
    expect(screen.queryByDisplayValue("注意力")).not.toBeInTheDocument();
    const topicName = screen.getByDisplayValue("Transformer"); fireEvent.change(topicName, { target: { value: "新版 Transformer" } });
    fireEvent.click(screen.getAllByRole("button", { name: "停用" })[0]); expect(screen.getAllByRole("button", { name: "启用" })[0]).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "添加知识点" }));
    expect(screen.queryByDisplayValue("新知识点")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "新知识点 展开" }));
    expect(screen.getByDisplayValue("新知识点")).toBeVisible();
    fireEvent.click(screen.getAllByRole("button", { name: "删除" })[2]);
    expect(screen.getByRole("alertdialog", { name: "删除知识点“新知识点”？" })).toBeVisible();
    expect(screen.getByDisplayValue("新知识点")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    expect(screen.queryByDisplayValue("新知识点")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存教学目录" }));
    await waitFor(() => expect(updateTeacherCatalog).toHaveBeenCalledWith("default", expect.objectContaining({ topics: [expect.objectContaining({ name: "新版 Transformer", status: "disabled" })] })));
  });

  it("creates, edits status and deletes an exercise blueprint", async () => {
    history.replaceState({}, "", "/teacher/exercises"); render(<TeacherWorkspace />);
    fireEvent.change(await screen.findByLabelText("exercise蓝图名称"), { target: { value: "Attention 练习" } });
    fireEvent.change(screen.getByLabelText("exercise所属主题"), { target: { value: "transformer" } });
    fireEvent.change(screen.getByLabelText("exercise关联知识点"), { target: { value: "attention" } });
    fireEvent.change(screen.getByLabelText("exerciseMarkdown 指令"), { target: { value: "考察 Q、K、V 的作用。" } });
    fireEvent.click(screen.getByRole("button", { name: "创建单题草稿蓝图" }));
    expect(screen.queryByDisplayValue("Attention 练习")).not.toBeInTheDocument(); expect(screen.getByText("草稿")).toBeVisible();
    expect(screen.getByLabelText("主题：Transformer")).toBeVisible();
    expect(screen.getByText("知识点")).toBeVisible();
    expect(screen.getByText("1 张蓝图")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Attention 练习 展开" }));
    expect(screen.getByDisplayValue("Attention 练习")).toBeVisible();
    expect(screen.queryByText("解释难度")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存教学目录" }));
    await waitFor(() => expect(updateTeacherCatalog).toHaveBeenCalledWith("default", expect.objectContaining({
      exercise_blueprints: [expect.not.objectContaining({ level: expect.anything() })],
    })));
    fireEvent.click(screen.getByRole("button", { name: "启用" })); expect(screen.getByRole("button", { name: "停用" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "删除" })); expect(screen.getByDisplayValue("Attention 练习")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "确认删除" })); expect(screen.queryByDisplayValue("Attention 练习")).not.toBeInTheDocument();
  });

  it("creates and deletes a review blueprint with its own structured fields", async () => {
    history.replaceState({}, "", "/teacher/reviews"); render(<TeacherWorkspace />);
    fireEvent.change(await screen.findByLabelText("review蓝图名称"), { target: { value: "Transformer 复习" } });
    fireEvent.change(screen.getByLabelText("review所属主题"), { target: { value: "transformer" } });
    fireEvent.change(screen.getByLabelText("review关联知识点"), { target: { value: "attention" } });
    fireEvent.change(screen.getByLabelText("reviewMarkdown 指令"), { target: { value: "先回顾再练习。" } });
    fireEvent.click(screen.getByRole("button", { name: "创建单题草稿蓝图" }));
    expect(screen.queryByDisplayValue("Transformer 复习")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Transformer 复习 展开" }));
    expect(screen.getByDisplayValue("Transformer 复习")).toBeVisible(); expect(screen.getAllByDisplayValue("简答")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    expect(screen.queryByDisplayValue("Transformer 复习")).not.toBeInTheDocument();
  });

  it("creates an editable guided blueprint with a Markdown direction", async () => {
    history.replaceState({}, "", "/teacher/guided"); render(<TeacherWorkspace />);
    fireEvent.change(await screen.findByLabelText("guided蓝图名称"), { target: { value: "QKV 追问路径" } });
    fireEvent.change(screen.getByLabelText("guided所属主题"), { target: { value: "transformer" } });
    fireEvent.change(screen.getByLabelText("guided关联知识点"), { target: { value: "attention" } });
    fireEvent.change(screen.getByLabelText("guidedMarkdown 指令"), { target: { value: "先让学生区分 Q、K、V，再追问权重。" } });
    fireEvent.click(screen.getByRole("button", { name: "创建引导草稿蓝图" }));
    fireEvent.click(screen.getByRole("button", { name: "QKV 追问路径 展开" }));
    expect(screen.getByDisplayValue("先让学生区分 Q、K、V，再追问权重。")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "保存教学目录" }));
    await waitFor(() => expect(updateTeacherCatalog).toHaveBeenCalledWith("default", expect.objectContaining({ guided_blueprints: [expect.objectContaining({ guidance: "先让学生区分 Q、K、V，再追问权重。" })] })));
  });

  it("renders guided mode when a legacy catalog has no guided_blueprints field", () => {
    render(<GuidedBlueprintCatalogEditor topics={[{ id: "legacy", name: "旧主题", description: "", status: "enabled", knowledge_points: [{ id: "kp", name: "旧知识点", markdown: "", status: "enabled", sort_order: 0 }] }]} blueprints={undefined as unknown as []} onChange={vi.fn()} />);
    expect(screen.getByText("引导蓝图目录")).toBeVisible();
  });

  it("presents manual catalog creation without a preset import action", async () => {
    history.replaceState({}, "", "/teacher/topics"); render(<TeacherWorkspace />);
    expect(await screen.findByRole("button", { name: "创建主题" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "导入 NLP 课件" })).not.toBeInTheDocument();
    expect(screen.getByText("主题、知识点和蓝图均由教师手动创建并保存；不会导入预置课程数据。")).toBeVisible();
  });

  it("renders question statistics without raw question text", async () => {
    history.replaceState({}, "", "/teacher/questions"); render(<TeacherWorkspace />);
    expect(await screen.findByText("从提问统计发现教学线索")).toBeVisible();
    expect(screen.getByText("主题分布")).toBeVisible();
    expect(screen.getByText("模式分布")).toBeVisible();
    expect(screen.queryByText("BLEU 的长度惩罚怎么计算？")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("搜索学生问题")).not.toBeInTheDocument();
  });

  it("renders evidence-based risk and knowledge-point stats", async () => {
    history.replaceState({}, "", "/teacher/reports"); render(<TeacherWorkspace />);
    expect(await screen.findByText("从练习证据发现薄弱项")).toBeVisible();
    expect(screen.getByText("主题健康度")).toBeVisible();
    expect(screen.getByText("知识点掌握情况")).toBeVisible();
    expect(screen.getByText("注意力")).toBeVisible();
  });

  it("updates the visible page when a nested teacher route changes without a full reload", async () => {
    render(
      <MemoryRouter initialEntries={["/teacher/topics"]}>
        <Routes>
          <Route path="/teacher/*" element={<TeacherRoutes />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("button", { name: "主题与知识点" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "学生问题" }));

    expect(await screen.findByText("从提问统计发现教学线索")).toBeVisible();
  });
});
