import { useState } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { BlueprintCatalogEditor, GuidedBlueprintCatalogEditor, TopicCatalogEditor } from "./TeacherCatalogEditor";
import { TeacherWorkspace } from "./TeacherWorkspace";
import { TeacherRoutes } from "../routes";
import type { CourseTopic } from "@/shared/types";

const { ensureAuthMock, getSettingsMock, getTeacherCatalog, getTeacherOverviewMock, updateTeacherCatalog } = vi.hoisted(() => ({
  ensureAuthMock: vi.fn(),
  getSettingsMock: vi.fn(),
  getTeacherCatalog: vi.fn().mockResolvedValue({ catalog: { workspace_id: "default", topics: [{ id: "transformer", name: "Transformer", description: "", status: "enabled", knowledge_points: [{ id: "attention", name: "注意力", markdown: "# Attention", status: "enabled", sort_order: 0 }] }], exercise_blueprints: [], review_blueprints: [], guided_blueprints: [] } }),
  getTeacherOverviewMock: vi.fn().mockResolvedValue({ workspace_id: "default", period_days: 30, summary: { questions: 2, sessions: 2, students: 2, error_questions: 0, exercises: 3, exercise_pass_rate: 66.67, guided_sessions: 1 }, weak_topics: [{ topic_id: "transformer", topic: "Transformer", questions: 2, errors: 0, exercises: 3, average_score: 70, pass_rate: 66.67, misconceptions: 1, risk: "medium" }], topic_distribution: [{ name: "Transformer", count: 2, percentage: 100 }], difficulty_distribution: [{ name: "入门", count: 2, percentage: 100 }], mode_distribution: [{ name: "讲解", count: 2, percentage: 100 }], daily_questions: [{ date: "2026-07-19", count: 2 }], knowledge_point_stats: [{ knowledge_point_id: "attention", name: "注意力", topic: "Transformer", exercises: 3, average_score: 70, pass_rate: 66.67, weak_criteria: [{ criterion: "概念准确", hit_rate: 100 }, { criterion: "步骤完整", hit_rate: 33.33 }] }],
  }),
  updateTeacherCatalog: vi.fn().mockImplementation(async (_workspaceId, next) => ({ catalog: { workspace_id: "default", ...next } })),
}));
vi.mock("@/platform/http/api", () => ({
  ensureAuth: ensureAuthMock,
  api: { getSettings: getSettingsMock, getTeacherOverview: getTeacherOverviewMock, getTeacherCatalog, updateTeacherCatalog },
}));

describe("TeacherWorkspace catalog CRUD", () => {
  beforeEach(() => {
    updateTeacherCatalog.mockClear();
    getTeacherCatalog.mockClear();
    getTeacherOverviewMock.mockClear();
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

    const menu = screen.getByRole("button", { name: "Transformer目录选项" }).closest("details") as HTMLDetailsElement;
    menu.open = true;
    fireEvent.pointerDown(document.body);
    expect(menu.open).toBe(false);

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
