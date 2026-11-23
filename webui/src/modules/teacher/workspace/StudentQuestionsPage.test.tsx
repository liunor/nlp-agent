import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StudentQuestionsPage } from "./StudentQuestionsPage";
import type { TeacherOverview } from "@/shared/types";

const mockOverview: TeacherOverview = {
  workspace_id: "default",
  period_days: 30,
  goals: {
    workspace_id: "default",
    course_title: "NLP 核心课程",
    description: "",
    objectives: [],
    focus_topics: [],
    target_level: "intermediate",
  },
  annotations: {
    workspace_id: "default",
    focused: [],
    ignored: [],
    notes: {},
  },
  revision: 1,
  updated_at: "2026-11-23T00:00:00Z",
  summary: {
    questions: 100,
    sessions: 40,
    students: 3,
    active_days: 15,
    error_questions: 5,
    error_rate: 5.0,
    questions_per_student: 33.33,
    questions_per_session: 2.5,
    contextualized_questions: 90,
    context_coverage_rate: 90.0,
    exercises: 20,
    exercise_pass_rate: 80.0,
    guided_sessions: 8,
  },
  topic_distribution: [
    { name: "Transformer 与注意力机制", count: 60, percentage: 60.0 },
    { name: "词向量与表示学习", count: 40, percentage: 40.0 },
  ],
  difficulty_distribution: [
    { name: "进阶", count: 70, percentage: 70.0 },
    { name: "入门", count: 30, percentage: 30.0 },
  ],
  mode_distribution: [
    { name: "讲解", count: 60, percentage: 60.0 },
    { name: "练习", count: 40, percentage: 40.0 },
  ],
  daily_questions: [
    { date: "2026-11-01", count: 5 },
    { date: "2026-11-02", count: 8 },
  ],
  hourly_questions: Array.from({ length: 24 }, (_, hour) => ({
    hour,
    label: `${String(hour).padStart(2, "0")}:00`,
    count: hour === 14 ? 20 : 2,
    percentage: hour === 14 ? 20.0 : 2.0,
  })),
  weekday_questions: Array.from({ length: 7 }, (_, weekday) => ({
    weekday,
    label: ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][weekday],
    count: 14,
    percentage: 14.28,
  })),
  peak_day: { date: "2026-11-02", count: 8 },
  peak_hour: { hour: 14, label: "14:00", count: 20 },
  student_activity: [
    {
      user_id: "s1",
      display_name: "张三",
      username: "zhangsan",
      questions: 50,
      sessions: 20,
      active_days: 10,
      error_questions: 2,
      error_rate: 4.0,
      questions_per_session: 2.5,
      last_active: "2026-11-20",
      top_topic: "Transformer 与注意力机制",
    },
    {
      user_id: "s2",
      display_name: "李四",
      username: "lisi",
      questions: 30,
      sessions: 12,
      active_days: 8,
      error_questions: 1,
      error_rate: 3.33,
      questions_per_session: 2.5,
      last_active: "2026-11-18",
      top_topic: "词向量与表示学习",
    },
    {
      user_id: "s3",
      display_name: "王五",
      username: "wangwu",
      questions: 0,
      sessions: 0,
      active_days: 0,
      error_questions: 0,
      error_rate: 0,
      questions_per_session: 0,
      last_active: null,
      top_topic: "暂无提问",
    },
  ],
  weak_topics: [],
  knowledge_point_stats: [],
};

describe("StudentQuestionsPage Real Student Activity Enhancements", () => {
  it("renders P0 student activity table with search, filter tabs, and sorting", () => {
    render(<StudentQuestionsPage data={mockOverview} />);

    // Check Filter tabs
    expect(screen.getByText(/全部学生 \(3\)/)).toBeVisible();
    expect(screen.getByText(/提问活跃 \(2\)/)).toBeVisible();
    expect(screen.getByText(/沉默待关怀 \(1\)/)).toBeVisible();

    // Initial table view displays all 3 students
    expect(screen.getByText("张三")).toBeVisible();
    expect(screen.getByText("李四")).toBeVisible();
    expect(screen.getByText("王五")).toBeVisible();

    // Silent badge for 王五
    expect(screen.getByText("零提问 · 待关怀")).toBeVisible();

    // Test Search by name
    const searchInput = screen.getByLabelText("搜索学生姓名或学号");
    fireEvent.change(searchInput, { target: { value: "李四" } });
    expect(screen.getByText("李四")).toBeVisible();
    expect(screen.queryByText("张三")).not.toBeInTheDocument();
    expect(screen.queryByText("王五")).not.toBeInTheDocument();

    // Clear search
    fireEvent.click(screen.getByLabelText("清除搜索"));
    expect(screen.getByText("张三")).toBeVisible();
    expect(screen.getByText("王五")).toBeVisible();

    // Test Filter by silent students
    fireEvent.click(screen.getByText(/沉默待关怀/));
    expect(screen.getByText("王五")).toBeVisible();
    expect(screen.queryByText("张三")).not.toBeInTheDocument();
    expect(screen.queryByText("李四")).not.toBeInTheDocument();
    expect(screen.getByText(/共发现/)).toBeVisible();
    expect(screen.getByText(/名学生在统计周期内未发起任何提问/)).toBeVisible();

    // Switch back to active students
    fireEvent.click(screen.getByText(/提问活跃/));
    expect(screen.getByText("张三")).toBeVisible();
    expect(screen.getByText("李四")).toBeVisible();
    expect(screen.queryByText("王五")).not.toBeInTheDocument();

    // Test sorting by column
    fireEvent.click(screen.getByText(/全部学生/));
    const questionsHeader = screen.getByRole("columnheader", { name: /问题量/ });
    fireEvent.click(questionsHeader); // click to sort
    expect(screen.getByText("张三")).toBeVisible();
  });

  it("adapts to personLabel in all_users mode and supports empty search state", () => {
    const userOverview: TeacherOverview = {
      ...mockOverview,
      data_scope: {
        mode: "all_users",
        label: "所有人",
        classroom_count: null,
        student_count: 3,
        update_policy: "on_refresh",
        message: "",
      },
    };
    render(<StudentQuestionsPage data={userOverview} />);

    // personLabel adapts to "用户"
    expect(screen.getByText("用户参与度")).toBeVisible();
    expect(screen.getByText(/全部用户 \(3\)/)).toBeVisible();
    expect(screen.getByPlaceholderText("搜索用户姓名或学号...")).toBeVisible();

    // Test empty search state
    const searchInput = screen.getByLabelText("搜索用户姓名或学号");
    fireEvent.change(searchInput, { target: { value: "不存在的人" } });
    expect(screen.getByText("未搜索到与“不存在的人”匹配的用户。")).toBeVisible();
  });
});
