import { useState } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { BlueprintCatalogEditor, GuidedBlueprintCatalogEditor, TopicCatalogEditor } from "./TeacherCatalogEditor";
import { LearningAnalysisPage } from "./LearningAnalysisPage";
import { TeacherWorkspace } from "./TeacherWorkspace";
import { TeacherRoutes } from "../routes";
import type { CourseTopic } from "@/shared/types";

const { ensureAuthMock, generateTeacherAIAnalysisMock, getSettingsMock, getTeacherCatalog, getTeacherOverviewMock, updateTeacherCatalog } = vi.hoisted(() => ({
  ensureAuthMock: vi.fn(),
  generateTeacherAIAnalysisMock: vi.fn(),
  getSettingsMock: vi.fn(),
  getTeacherCatalog: vi.fn().mockResolvedValue({ catalog: { workspace_id: "default", topics: [{ id: "transformer", name: "Transformer", description: "", status: "enabled", knowledge_points: [{ id: "attention", name: "注意力", markdown: "# Attention", status: "enabled", sort_order: 0 }] }], exercise_blueprints: [], review_blueprints: [], guided_blueprints: [] } }),
  getTeacherOverviewMock: vi.fn().mockResolvedValue({ workspace_id: "default", period_days: 30, summary: { questions: 2, sessions: 2, students: 9, active_days: 2, error_questions: 0, error_rate: 0, questions_per_student: 1, questions_per_session: 1, contextualized_questions: 2, context_coverage_rate: 100, exercises: 3, exercise_pass_rate: 66.67, guided_sessions: 1 }, student_activity: Array.from({ length: 9 }, (_, index) => ({ user_id: `u${index + 1}`, display_name: index === 0 ? "张三" : `学生${index + 1}`, username: `student${index + 1}`, questions: index === 0 ? 2 : 1, sessions: 1, active_days: 1, error_questions: 0, error_rate: 0, questions_per_session: index === 0 ? 2 : 1, last_active: "2026-07-19", top_topic: "Transformer" })), hourly_questions: [{ hour: 9, label: "09:00", count: 2, percentage: 100 }], weekday_questions: [{ weekday: 0, label: "星期一", count: 2, percentage: 100 }], peak_day: { date: "2026-07-19", count: 2 }, peak_hour: { hour: 9, label: "09:00", count: 2 }, weak_topics: [{ topic_id: "transformer", topic: "Transformer", questions: 2, errors: 0, exercises: 3, average_score: 70, pass_rate: 66.67, misconceptions: 1, risk: "medium" }], topic_distribution: [{ name: "Transformer", count: 2, percentage: 100 }], difficulty_distribution: [{ name: "入门", count: 2, percentage: 100 }], mode_distribution: [{ name: "讲解", count: 2, percentage: 100 }], daily_questions: [{ date: "2026-07-19", count: 2 }], knowledge_point_stats: [{ knowledge_point_id: "attention", name: "注意力", topic: "Transformer", exercises: 3, average_score: 70, pass_rate: 66.67, weak_criteria: [{ criterion: "概念准确", hit_rate: 100 }, { criterion: "步骤完整", hit_rate: 33.33 }] }], learning_analysis: { scope: { period_days: 30, period_label: "近 30 天", role_label: "学生", student_count: 9, attempt_count: 8 }, conclusions: { weak: { content_id: "transformer", content_name: "Transformer", knowledge_point_id: "attention", knowledge_point_name: "注意力", question_count: 4, student_count: 6, attempt_count: 4, correct_count: 2, mastery_rate: 50, previous_mastery_rate: 68, trend: "down", problem_type: "概念掌握不足", data_sufficiency: "sufficient", average_score: 50, weak_criteria: [{ criterion: "定义域判断", error_rate: 75 }], concern_score: 70, recommendation: { conclusion: "注意力当前掌握率为 50%", action: "补充概念示例和变式练习" } }, declining: { content_id: "transformer", content_name: "Transformer", knowledge_point_id: "attention", knowledge_point_name: "注意力", question_count: 4, student_count: 6, attempt_count: 4, correct_count: 2, mastery_rate: 50, previous_mastery_rate: 68, trend: "down", problem_type: "概念掌握不足", data_sufficiency: "sufficient", average_score: 50, weak_criteria: [], concern_score: 70, recommendation: { conclusion: "注意力近期下降", action: "安排复习" } }, good: { content_id: "transformer", content_name: "Transformer", knowledge_point_id: "位置编码", question_count: 4, student_count: 6, attempt_count: 4, correct_count: 4, mastery_rate: 100, previous_mastery_rate: 88, trend: "up", problem_type: "—", data_sufficiency: "sufficient", average_score: 90, weak_criteria: [], concern_score: 0, recommendation: { conclusion: "位置编码掌握较好", action: "继续观察" } } }, diagnoses: [{ content_id: "transformer", content_name: "Transformer", knowledge_point_id: "attention", knowledge_point_name: "注意力", question_count: 4, student_count: 6, attempt_count: 4, correct_count: 2, mastery_rate: 50, previous_mastery_rate: 68, trend: "down", problem_type: "概念掌握不足", data_sufficiency: "sufficient", average_score: 50, weak_criteria: [{ criterion: "定义域判断", error_rate: 75 }], concern_score: 70, recommendation: { conclusion: "注意力当前掌握率为 50%", action: "补充概念示例和变式练习" } }, { content_id: "transformer", content_name: "Transformer", knowledge_point_id: "位置编码", knowledge_point_name: "位置编码", question_count: 4, student_count: 6, attempt_count: 4, correct_count: 4, mastery_rate: 100, previous_mastery_rate: 88, trend: "up", problem_type: "—", data_sufficiency: "sufficient", average_score: 90, weak_criteria: [], concern_score: 0, recommendation: { conclusion: "位置编码掌握较好", action: "继续观察" } }], problem_distribution: [{ name: "概念掌握不足", count: 1, percentage: 50 }, { name: "解题方法不熟", count: 0, percentage: 0 }, { name: "易错点集中", count: 0, percentage: 0 }, { name: "练习覆盖不足", count: 0, percentage: 0 }, { name: "学习参与不足", count: 0, percentage: 0 }, { name: "数据不足，暂不判断", count: 0, percentage: 0 }], mastery_trend: { months: [{ month: "2026-04", label: "2026年04月" }, { month: "2026-05", label: "2026年05月" }, { month: "2026-06", label: "2026年06月" }, { month: "2026-07", label: "2026年07月" }, { month: "2026-08", label: "2026年08月" }], series: [{ knowledge_point_id: "attention", name: "注意力", values: [62, 65, 72, 68, 50] }, { knowledge_point_id: "posenc", name: "位置编码", values: [74, 79, 84, 88, 100] }] } },
  }),
  updateTeacherCatalog: vi.fn().mockImplementation(async (_workspaceId, next) => ({ catalog: { workspace_id: "default", ...next } })),
}));
vi.mock("@/platform/http/api", () => ({
  ensureAuth: ensureAuthMock,
  api: { getSettings: getSettingsMock, getTeacherOverview: getTeacherOverviewMock, getTeacherCatalog, updateTeacherCatalog, generateTeacherAIAnalysis: generateTeacherAIAnalysisMock },
}));

describe("TeacherWorkspace catalog CRUD", () => {
  beforeEach(() => {
    updateTeacherCatalog.mockClear();
    getTeacherCatalog.mockClear();
    getTeacherOverviewMock.mockClear();
    generateTeacherAIAnalysisMock.mockClear();
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

  it("places the knowledge book entry between home and topic management", async () => {
    history.replaceState({}, "", "/teacher");
    render(<TeacherWorkspace />);

    expect(await screen.findByRole("button", { name: "教材内容" })).toBeVisible();
    const home = screen.getByRole("button", { name: "教师首页" });
    const book = screen.getByRole("button", { name: "教材内容" });
    const topics = screen.getByRole("button", { name: "主题与知识点" });
    expect(home.compareDocumentPosition(book) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(book.compareDocumentPosition(topics) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("updates the visible page when the routed page changes", async () => {
    const view = render(<TeacherWorkspace page="topics" />);

    expect(await screen.findByRole("heading", { name: "主题与知识点" })).toBeVisible();
    view.rerender(<TeacherWorkspace page="questions" />);

    expect(await screen.findByRole("heading", { name: "学生问题" })).toBeVisible();
  });

  it("does not load analytics data for catalog editor pages", async () => {
    history.replaceState({}, "", "/teacher/topics"); render(<TeacherWorkspace />);

    expect(await screen.findByRole("heading", { name: "主题与知识点" })).toBeVisible();
    expect(getTeacherOverviewMock).not.toHaveBeenCalled();
  });

  it("creates a topic in the shared editor and persists the catalog through FastAPI", async () => {
    history.replaceState({}, "", "/teacher/topics"); render(<TeacherWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "新建主题" }));
    fireEvent.change(screen.getByLabelText("主题名称"), { target: { value: "词向量" } });
    expect(screen.getByDisplayValue("词向量")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "保存教学目录" }));
    await waitFor(() => expect(updateTeacherCatalog).toHaveBeenCalledWith("default", expect.objectContaining({ topics: expect.arrayContaining([expect.objectContaining({ name: "词向量" })]) })));
    expect(screen.getByRole("status")).toHaveTextContent("已保存并同步到后端。");
  });

  it("starts with topic groups collapsed while keeping the selected editor visible", async () => {
    history.replaceState({}, "", "/teacher/topics"); render(<TeacherWorkspace />);

    expect(await screen.findByRole("heading", { name: "主题与知识点" })).toBeVisible();
    expect(screen.getByDisplayValue("Transformer")).toBeVisible();
    expect(screen.getByRole("button", { name: "展开主题 Transformer" })).toHaveAttribute("aria-expanded", "false");
  });

  it("sorts disabled knowledge points to the end and keeps enabled rows unlabelled", () => {
    render(<TopicCatalogEditor topics={[{ id: "topic", name: "主题", description: "", status: "enabled", knowledge_points: [
      { id: "enabled", name: "可用知识点", markdown: "", status: "enabled", sort_order: 0 },
      { id: "disabled", name: "停用知识点", markdown: "", status: "disabled", sort_order: 1 },
    ] }]} onChange={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "展开主题 主题" }));
    expect(screen.getAllByRole("button", { name: /选择知识点/ }).map((button) => button.getAttribute("aria-label"))).toEqual([
      "选择知识点 可用知识点",
      "选择知识点 停用知识点",
    ]);
    expect(within(screen.getByRole("complementary", { name: "主题与知识点目录" })).queryByText("已启用")).not.toBeInTheDocument();
  });

  it("closes an open directory menu when clicking outside and restores the directory after collapsing", async () => {
    history.replaceState({}, "", "/teacher/topics"); render(<TeacherWorkspace />);
    await screen.findByRole("heading", { name: "主题与知识点", level: 2 });

    const user = userEvent.setup();
    const summary = screen.getByRole("button", { name: "Transformer目录选项" });
    const menu = summary.closest("details") as HTMLDetailsElement;
    await user.click(summary);
    expect(menu.open).toBe(true);
    await user.click(document.body);
    await waitFor(() => expect(menu.open).toBe(false));

    fireEvent.click(screen.getByRole("button", { name: "收起主题与知识点目录" }));
    const expandButton = screen.getByRole("button", { name: "展开主题与知识点目录" });
    expect(screen.getByRole("complementary", { name: "主题与知识点目录" })).toHaveClass("collapsed");
    fireEvent.click(expandButton);
    expect(screen.getByRole("button", { name: "收起主题与知识点目录" })).toBeVisible();
  });

  it("keeps the Markdown toolbar read-only while previewing", async () => {
    history.replaceState({}, "", "/teacher/topics"); render(<TeacherWorkspace />);

    expect(await screen.findByRole("button", { name: "展开主题 Transformer" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "展开主题 Transformer" }));
    expect(screen.getByRole("button", { name: "选择知识点 注意力" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "选择知识点 注意力" }));
    fireEvent.click(screen.getByRole("button", { name: "预览正文" }));

    expect(screen.getByRole("button", { name: "加粗" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "返回编辑" })).toBeVisible();
  });

  it("stores configurable question types on a knowledge point", () => {
    function ControlledTopicEditor() {
      const [topics, setTopics] = useState<CourseTopic[]>([{ id: "topic", name: "主题", description: "", status: "enabled", knowledge_points: [{ id: "point", name: "知识点", markdown: "", status: "enabled", sort_order: 0 }] }]);
      return <TopicCatalogEditor topics={topics} onChange={setTopics} />;
    }
    render(<ControlledTopicEditor />);

    fireEvent.click(screen.getByRole("button", { name: "展开主题 主题" }));
    fireEvent.click(screen.getByRole("button", { name: "选择知识点 知识点" }));
    expect(screen.getByRole("group", { name: "知识点可用题型" })).toBeVisible();
    fireEvent.click(screen.getByRole("checkbox", { name: "代码阅读题" }));
    fireEvent.change(screen.getByRole("textbox", { name: "自定义题型" }), { target: { value: "实验设计题" } });
    fireEvent.click(screen.getByRole("button", { name: "添加" }));

    expect(screen.getByRole("checkbox", { name: "代码阅读题" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "实验设计题" })).toBeChecked();
  });

  it("uses the selected knowledge point question types in a blueprint", () => {
    render(<BlueprintCatalogEditor kind="exercise" topics={[{ id: "topic", name: "主题", description: "", status: "enabled", knowledge_points: [{ id: "point", name: "知识点", markdown: "", status: "enabled", sort_order: 0, question_types: ["代码阅读题", "实验设计题"] }] }]} blueprints={[{ id: "blueprint", name: "蓝图", topic_id: "topic", knowledge_point_id: "point", instructions: "说明", question_type: "代码阅读题", status: "draft", rubric: [] }]} onChange={vi.fn()} />);

    const questionType = screen.getByRole("combobox", { name: "exercise题型" });
    expect(questionType).toHaveValue("代码阅读题");
    expect(within(questionType).getByRole("option", { name: "实验设计题" })).toBeInTheDocument();
    expect(within(questionType).queryByRole("option", { name: "选择题" })).not.toBeInTheDocument();
  });

  it("warns before leaving a teacher page with unsaved edits", async () => {
    history.replaceState({}, "", "/teacher/topics");
    render(<TeacherWorkspace />);
    await screen.findByRole("heading", { name: "主题与知识点", level: 2 });
    fireEvent.change(screen.getByLabelText("主题名称"), { target: { value: "未保存主题" } });

    fireEvent.click(screen.getByRole("button", { name: "出题蓝图" }));
    expect(screen.getByRole("alertdialog", { name: "有未保存的修改" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "留在当前页面" }));
    expect(screen.getByRole("heading", { name: "主题与知识点", level: 2 })).toBeVisible();

    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "出题蓝图" }));
    fireEvent.click(screen.getByRole("button", { name: "继续离开" }));
    expect(await screen.findByRole("heading", { name: "出题蓝图", level: 2 })).toBeVisible();
  });

  it("edits, disables, creates and deletes knowledge points", async () => {
    history.replaceState({}, "", "/teacher/topics"); render(<TeacherWorkspace />);
    expect(await screen.findByRole("button", { name: "展开主题 Transformer" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "展开主题 Transformer" }));
    expect(screen.getByRole("button", { name: "选择知识点 注意力" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "选择知识点 注意力" }));
    expect(screen.getByDisplayValue("注意力")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "选择主题 Transformer" }));
    const topicName = screen.getByDisplayValue("Transformer"); fireEvent.change(topicName, { target: { value: "新版 Transformer" } });
    const topicMenu = screen.getByRole("button", { name: "新版 Transformer目录选项" }).closest("details"); expect(topicMenu).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "新版 Transformer目录选项" }));
    fireEvent.click(within(topicMenu as HTMLElement).getByRole("button", { name: "停用主题" }));
    expect(screen.getByRole("button", { name: "新版 Transformer目录选项" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "新版 Transformer目录选项" }));
    fireEvent.click(within(topicMenu as HTMLElement).getByRole("button", { name: "新增知识点" }));
    expect(screen.getByDisplayValue("新知识点")).toBeVisible();
    const pointMenu = screen.getByRole("button", { name: "新知识点选项" }).closest("details"); expect(pointMenu).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "新知识点选项" }));
    fireEvent.click(within(pointMenu as HTMLElement).getByRole("button", { name: "删除知识点" }));
    expect(screen.getByRole("alertdialog", { name: "删除知识点“新知识点”？" })).toBeVisible();
    expect(screen.getByDisplayValue("新知识点")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    expect(screen.queryByDisplayValue("新知识点")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存教学目录" }));
    await waitFor(() => expect(updateTeacherCatalog).toHaveBeenCalledWith("default", expect.objectContaining({ topics: [expect.objectContaining({ name: "新版 Transformer", status: "disabled" })] })));
  });

  it("creates, edits status and deletes an exercise blueprint", async () => {
    history.replaceState({}, "", "/teacher/exercises"); render(<TeacherWorkspace />);
    await screen.findByRole("heading", { name: "出题蓝图", level: 2 });
    expect(screen.queryByRole("button", { name: "出题蓝图目录选项" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "展开主题 Transformer" }));
    const pointCreateMenu = screen.getByRole("button", { name: "注意力出题蓝图选项" }).closest("details"); expect(pointCreateMenu).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "注意力出题蓝图选项" }));
    fireEvent.click(within(pointCreateMenu as HTMLElement).getByRole("button", { name: "新建出题蓝图" }));
    expect(screen.getByRole("heading", { name: "注意力 · 出题蓝图", level: 3 })).toBeVisible();
    expect(screen.queryByLabelText("exercise蓝图名称")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("exercise所属主题")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("exercise关联知识点")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("exerciseMarkdown 指令"), { target: { value: "考察 Q、K、V 的作用。" } });
    expect(screen.getAllByText("草稿").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "折叠主题 Transformer" })).toBeVisible();
    expect(screen.getByText("Transformer · 注意力")).toBeVisible();
    expect(screen.getByRole("heading", { name: "出题蓝图", level: 2 })).toBeVisible();
    expect(screen.queryByText("解释难度")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存教学目录" }));
    await waitFor(() => expect(updateTeacherCatalog).toHaveBeenCalledWith("default", expect.objectContaining({
      exercise_blueprints: [expect.not.objectContaining({ level: expect.anything() })],
    })));
    const blueprintMenu = screen.getByRole("button", { name: "注意力 · 出题蓝图选项" }).closest("details"); expect(blueprintMenu).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "注意力 · 出题蓝图选项" }));
    fireEvent.click(within(blueprintMenu as HTMLElement).getByRole("button", { name: "启用出题蓝图" }));
    fireEvent.click(screen.getByRole("button", { name: "注意力 · 出题蓝图选项" }));
    fireEvent.click(within(blueprintMenu as HTMLElement).getByRole("button", { name: "删除出题蓝图" })); expect(screen.getByRole("alertdialog", { name: "删除出题蓝图“注意力 · 出题蓝图”？" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "确认删除" })); expect(screen.queryByRole("heading", { name: "注意力 · 出题蓝图", level: 3 })).not.toBeInTheDocument();
  });

  it("creates and deletes a review blueprint with its own structured fields", async () => {
    history.replaceState({}, "", "/teacher/reviews"); render(<TeacherWorkspace />);
    await screen.findByRole("heading", { name: "复习蓝图", level: 2 });
    fireEvent.click(screen.getByRole("button", { name: "展开主题 Transformer" }));
    const pointCreateMenu = screen.getByRole("button", { name: "注意力复习蓝图选项" }).closest("details"); expect(pointCreateMenu).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "注意力复习蓝图选项" }));
    fireEvent.click(within(pointCreateMenu as HTMLElement).getByRole("button", { name: "新建复习蓝图" }));
    expect(screen.getByRole("heading", { name: "注意力 · 复习蓝图", level: 3 })).toBeVisible();
    expect(screen.queryByLabelText("review所属主题")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("review关联知识点")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("reviewMarkdown 指令"), { target: { value: "先回顾再练习。" } });
    expect(screen.getAllByDisplayValue("简答")).toHaveLength(1);
    const blueprintMenu = screen.getByRole("button", { name: "注意力 · 复习蓝图选项" }).closest("details"); expect(blueprintMenu).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "注意力 · 复习蓝图选项" }));
    fireEvent.click(within(blueprintMenu as HTMLElement).getByRole("button", { name: "删除复习蓝图" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    expect(screen.queryByRole("heading", { name: "注意力 · 复习蓝图", level: 3 })).not.toBeInTheDocument();
  });

  it("creates an editable guided blueprint with a Markdown direction", async () => {
    history.replaceState({}, "", "/teacher/guided"); render(<TeacherWorkspace />);
    await screen.findByRole("heading", { name: "引导蓝图", level: 2 });
    fireEvent.click(screen.getByRole("button", { name: "展开主题 Transformer" }));
    const pointCreateMenu = screen.getByRole("button", { name: "注意力引导蓝图选项" }).closest("details"); expect(pointCreateMenu).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "注意力引导蓝图选项" }));
    fireEvent.click(within(pointCreateMenu as HTMLElement).getByRole("button", { name: "新建引导蓝图" }));
    expect(screen.getByRole("heading", { name: "注意力 · 引导蓝图", level: 3 })).toBeVisible();
    expect(screen.queryByLabelText("guided所属主题")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("guided关联知识点")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("guidedMarkdown 指令"), { target: { value: "先让学生区分 Q、K、V，再追问权重。" } });
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
    await screen.findByRole("heading", { name: "主题与知识点", level: 2 });
    const createMenu = screen.getByRole("button", { name: "主题与知识点目录选项" }).closest("details"); expect(createMenu).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "主题与知识点目录选项" }));
    expect(within(createMenu as HTMLElement).getByRole("button", { name: "新建主题" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "导入 NLP 课件" })).not.toBeInTheDocument();
    expect(screen.getByText(/维护学生学习范围与智能体可引用的知识边界/)).toBeVisible();
  });

  it("renders question statistics without raw question text", async () => {
    history.replaceState({}, "", "/teacher/questions"); render(<TeacherWorkspace />);
    expect(await screen.findByText("学生问题全景")).toBeVisible();
    expect(screen.getByText("主题分布")).toBeVisible();
    expect(screen.getByText("模式分布")).toBeVisible();
    expect(screen.queryByText("RBAC 角色分布")).not.toBeInTheDocument();
    expect(screen.getByText(/仅统计 RBAC=学生的账号/)).toBeVisible();
    expect(screen.getByText("学生参与度")).toBeVisible();
    expect(screen.getByText("张三")).toBeVisible();
    expect(screen.getByText("小时分布")).toBeVisible();
    expect(screen.getByText("星期分布")).toBeVisible();
    expect(screen.queryByText("BLEU 的长度惩罚怎么计算？")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("搜索学生问题")).not.toBeInTheDocument();
  });

  it("paginates the student activity table instead of forcing every account into one view", async () => {
    history.replaceState({}, "", "/teacher/questions"); render(<TeacherWorkspace />);

    expect(await screen.findByText("显示 1–8 / 共 9 名学生")).toBeVisible();
    expect(screen.getByText("学生8")).toBeVisible();
    expect(screen.queryByText("学生9")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));

    expect(screen.getByText("显示 9–9 / 共 9 名学生")).toBeVisible();
    expect(screen.getByText("学生9")).toBeVisible();
    expect(screen.queryByText("学生8")).not.toBeInTheDocument();
  });

  it("enables a local demo dataset through the explicit demo query flag", async () => {
    history.replaceState({}, "", "/teacher/questions?demo=1"); render(<TeacherWorkspace />);

    expect(await screen.findByText("本地演示数据 · 仅用于布局验收")).toBeVisible();
    expect(screen.getByText("显示 1–8 / 共 18 名学生")).toBeVisible();
  });

  it("previews five monthly distributions and renders both trends as line charts", async () => {
    history.replaceState({}, "", "/teacher/questions?demo=1"); render(<TeacherWorkspace />);

    expect(await screen.findByText("月度统计 · 近 5 个月")).toBeVisible();
    expect(screen.getAllByRole("tab")).toHaveLength(5);
    expect(screen.getByRole("img", { name: "问题量趋势折线图" })).toBeVisible();
    expect(screen.getByRole("img", { name: "小时提问趋势折线图" })).toBeVisible();
    expect(screen.getByRole("img", { name: "星期问题分布饼图" })).toBeVisible();
    expect(screen.getAllByText("显示前 5 类")).toHaveLength(3);
    expect(document.querySelectorAll(".teacher-question-distribution article")).toHaveLength(15);
  });

  it("keeps line charts readable with dynamic axis bounds and hover details", async () => {
    history.replaceState({}, "", "/teacher/questions?demo=1"); render(<TeacherWorkspace />);

    const chart = await screen.findByRole("img", { name: "问题量趋势折线图" });
    expect(chart).toHaveAttribute("data-raw-max");
    expect(chart).toHaveAttribute("data-axis-max");
    expect(Number(chart.getAttribute("data-axis-max"))).toBeGreaterThan(Number(chart.getAttribute("data-raw-max")));
    expect(chart.querySelectorAll(".teacher-question-line-point")).toHaveLength(0);

    const hoverTarget = chart.querySelector(".teacher-question-line-hover-target");
    expect(hoverTarget).not.toBeNull();
    fireEvent.mouseEnter(hoverTarget as Element);
    expect(screen.getByRole("tooltip")).toHaveTextContent("第 1 天");
    expect(screen.getByRole("tooltip")).toHaveTextContent("2026");
    fireEvent.mouseLeave(hoverTarget as Element);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("shows more x-axis detail when each trend chart has its own row", async () => {
    history.replaceState({}, "", "/teacher/questions?demo=1"); render(<TeacherWorkspace />);

    const dailyChart = await screen.findByRole("img", { name: "问题量趋势折线图" });
    const hourlyChart = screen.getByRole("img", { name: "小时提问趋势折线图" });
    expect(dailyChart).toHaveAttribute("data-label-step", "5");
    expect(hourlyChart).toHaveAttribute("data-label-step", "2");
    expect(hourlyChart.querySelectorAll(".teacher-question-line-x-label")).toHaveLength(12);
  });

  it("places weekday labels on pie callouts beside the chart", async () => {
    history.replaceState({}, "", "/teacher/questions?demo=1"); render(<TeacherWorkspace />);

    await screen.findByRole("img", { name: "星期问题分布饼图" });
    expect(document.querySelectorAll(".teacher-question-pie-callout")).toHaveLength(7);
    expect(document.querySelectorAll(".teacher-question-pie-label")).toHaveLength(7);
    expect(document.querySelector(".teacher-question-pie-legend")).toBeNull();
  });

  it("renders a content diagnosis report with student-only scope", async () => {
    history.replaceState({}, "", "/teacher/reports"); render(<TeacherWorkspace />);
    expect(await screen.findByText("基于学生学习表现，定位需要重点关注的教学内容")).toBeVisible();
    expect(screen.queryByText("AI CONTENT REPORT")).not.toBeInTheDocument();
    expect(screen.queryByText("CONTENT DIAGNOSIS")).not.toBeInTheDocument();
    expect(screen.queryByText("TEACHER MODE")).not.toBeInTheDocument();
    expect(screen.queryByText("Teacher workspace")).not.toBeInTheDocument();
    expect(screen.getByText("学生角色")).toBeVisible();
    expect(screen.getByText("当前分析范围：近 30 天 · 全部教材内容 · 学生角色 · 9 名学生 · 8 次作答")).toBeVisible();
    expect(screen.getByText("重点薄弱内容")).toBeVisible();
    expect(screen.getByText("近期下降内容")).toBeVisible();
    expect(screen.getByText("掌握较好内容")).toBeVisible();
    expect(screen.getByText("尚未生成 AI 内容分析")).toBeVisible();
    expect(screen.getByRole("button", { name: "生成 AI 内容分析" })).toBeVisible();
    expect(screen.getByText("知识点诊断")).toBeVisible();
    expect(screen.getAllByText("概念掌握不足").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("作答 4 · 正确 2 · 上期 68%")).toBeVisible();
    expect(screen.getAllByText("样本充足").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("img", { name: "内容掌握趋势折线图" })).toBeVisible();
    expect(screen.getByRole("img", { name: "内容问题类型分布横向条形图" })).toBeVisible();
    expect(screen.getAllByRole("button", { name: "查看详情 注意力" })).toHaveLength(2);
    fireEvent.change(screen.getByRole("combobox", { name: "时间范围" }), { target: { value: "60" } });
    await waitFor(() => expect(getTeacherOverviewMock).toHaveBeenCalledWith("default", 60));
  });

  it("keeps data-insufficient diagnoses collapsed instead of mixing them into the evidence list", async () => {
    const data = structuredClone(await getTeacherOverviewMock());
    const source = data.learning_analysis.diagnoses[0];
    data.learning_analysis.diagnoses = [
      ...data.learning_analysis.diagnoses,
      ...Array.from({ length: 7 }, (_, index) => ({
        ...source,
        knowledge_point_id: `insufficient-${index + 1}`,
        knowledge_point_name: `待补充知识点 ${index + 1}`,
        question_count: 0,
        student_count: 0,
        attempt_count: 0,
        correct_count: 0,
        mastery_rate: null,
        previous_mastery_rate: null,
        problem_type: "数据不足，暂不判断",
        data_sufficiency: "insufficient",
      })),
    ];

    render(<LearningAnalysisPage data={data} />);

    expect(screen.getByText("2 个有证据知识点 · 7 个数据不足")).toBeVisible();
    expect(document.querySelector(".teacher-analysis-insufficient-list")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "展开 7 个数据不足知识点" }));
    expect(document.querySelector(".teacher-analysis-insufficient-list")).toHaveTextContent("待补充知识点 1");
  });

  it("generates AI analysis only after a teacher action and expires it when filters change", async () => {
    generateTeacherAIAnalysisMock.mockResolvedValue({
      status: "completed",
      source: "deepseek",
      generated_at: "2026-08-30T10:00:00+08:00",
      model: "DeepSeek",
      model_id: "deepseek-v4-flash",
      summary: "本周期重点关注函数单调性，建议先复习定义域判断。",
      diagnoses: [{ knowledge_point_id: "attention", knowledge_point_name: "注意力", level: "high", problem: "定义域判断存在共性混淆。", evidence: ["掌握率 50%"], suggestions: ["回顾定义域与单调区间的关系"], confidence: "high", data_gaps: [] }],
    });
    history.replaceState({}, "", "/teacher/reports"); render(<TeacherWorkspace />);

    expect(generateTeacherAIAnalysisMock).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "生成 AI 内容分析" }));
    await waitFor(() => expect(generateTeacherAIAnalysisMock).toHaveBeenCalledWith("default", expect.objectContaining({ course_id: "all", content_scope: "all", period_days: 30, force_refresh: false })));
    expect(await screen.findByText("本周期重点关注函数单调性，建议先复习定义域判断。")).toBeVisible();
    expect(screen.getByText("模型：DeepSeek")).toBeVisible();

    fireEvent.change(screen.getByRole("combobox", { name: "内容范围" }), { target: { value: "注意力" } });
    expect(screen.getByText("当前筛选条件已变化，请重新生成分析")).toBeVisible();
  });

  it("keeps conclusions and charts within the selected content range", async () => {
    history.replaceState({}, "", "/teacher/reports"); render(<TeacherWorkspace />);

    await screen.findByRole("img", { name: "内容掌握趋势折线图" });
    fireEvent.change(screen.getByRole("combobox", { name: "内容范围" }), { target: { value: "位置编码" } });

    expect(document.querySelector(".teacher-analysis-line-legend")).toHaveTextContent("位置编码");
    expect(document.querySelector(".teacher-analysis-line-legend")).not.toHaveTextContent("注意力");
  });

  it("expands a diagnosis into evidence and teacher-controlled actions", async () => {
    history.replaceState({}, "", "/teacher/reports"); render(<TeacherWorkspace />);
    await screen.findByText("知识点诊断");

    fireEvent.click(screen.getByRole("button", { name: "查看建议 注意力" }));
    expect(screen.getByText("补充概念示例和变式练习")).toBeVisible();
    expect(screen.getByText("定义域判断 · 错误率 75%")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "标记已关注 注意力" }));
    expect(screen.getByRole("button", { name: "已标记关注 注意力" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "添加备注 注意力" }));
    expect(screen.getByRole("textbox", { name: "注意力备注" })).toBeVisible();
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

    expect(await screen.findByText("学生问题全景")).toBeVisible();
  });
});
