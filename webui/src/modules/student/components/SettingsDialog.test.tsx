import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { SettingsDialog } from "./SettingsDialog";
import { loadFeedback } from "@/shared/utils/feedback";
import { APP_VERSION } from "@/shared/version";
import type { UserSettings } from "@/shared/types";

const { getFeedbackDailyStateMock, getOwnFeedbackMock, listPublishedReleaseNotesMock, markOwnFeedbackReadMock, submitFeedbackMock } = vi.hoisted(() => ({ getFeedbackDailyStateMock: vi.fn(), getOwnFeedbackMock: vi.fn(), listPublishedReleaseNotesMock: vi.fn(), markOwnFeedbackReadMock: vi.fn(), submitFeedbackMock: vi.fn() }));
vi.mock("@/platform/http/api", () => ({ api: { getFeedbackDailyState: getFeedbackDailyStateMock, getOwnFeedback: getOwnFeedbackMock, listPublishedReleaseNotes: listPublishedReleaseNotesMock, markOwnFeedbackRead: markOwnFeedbackReadMock, submitFeedback: submitFeedbackMock } }));

const settings: UserSettings = {
  theme: "system",
  content_font_size: "medium",
  reduce_motion: false,
  locale: "zh-CN",
  show_reasoning: true,
  stream_render_interval_ms: 30,
  model_profile: "deepseek",
  default_workspace_id: "default",
};

const baseProps = {
  open: true,
  settings,
  learningContext: { topic_id: null as string | null, topic_name: "", level: "beginner" as const, mode: "explain" as const },
  roles: ["student"],
  onClose: () => {},
  onChange: () => {},
  onReset: () => {},
  onLearningContextChange: () => {},
  onOpenDeveloper: () => {},
  onOpenTeacher: () => {},
};

describe("SettingsDialog", () => {
  beforeEach(() => {
    localStorage.clear();
    listPublishedReleaseNotesMock.mockReset();
    listPublishedReleaseNotesMock.mockResolvedValue({ items: [] });
    getFeedbackDailyStateMock.mockReset();
    getFeedbackDailyStateMock.mockResolvedValue({ used: 0, remaining: 3, limit: 3, today_start_utc: "2026-08-31T00:00:00+00:00" });
    getOwnFeedbackMock.mockReset();
    getOwnFeedbackMock.mockResolvedValue({ thread_id: null, user_id: "u1", username: "student", display_name: "Student", status: "open", category: "other", priority: "medium", updated_at: null, messages: [] });
    markOwnFeedbackReadMock.mockReset();
    markOwnFeedbackReadMock.mockResolvedValue({ ok: true, updated: true });
    submitFeedbackMock.mockReset();
    submitFeedbackMock.mockResolvedValue({ thread_id: "thread-1", remaining: 2, daily_limit: 3 });
  });

  it("renders the current version from the build-injected constant", () => {
    render(<SettingsDialog {...baseProps} />);
    fireEvent.click(screen.getByRole("button", { name: "版本与更新" }));

    expect(screen.getByText(`NLP 学习助手 v${APP_VERSION}`)).toBeVisible();
    expect(screen.getByText("版本号随构建自动同步")).toBeVisible();
  });

  it("renders published release notes fetched from the backend", async () => {
    listPublishedReleaseNotesMock.mockResolvedValue({
      items: [{ id: "n1", version: "1.0.0", released_at: "2026-08-01T00:00:00", notes: ["新增发布说明功能"], status: "published" }],
    });
    render(<SettingsDialog {...baseProps} />);
    fireEvent.click(screen.getByRole("button", { name: "版本与更新" }));

    expect(await screen.findByText("v1.0.0")).toBeVisible();
    expect(screen.getByText("新增发布说明功能")).toBeVisible();
    expect(listPublishedReleaseNotesMock).toHaveBeenCalledOnce();
  });

  it("shows an empty state when the backend has no published notes", async () => {
    render(<SettingsDialog {...baseProps} />);
    fireEvent.click(screen.getByRole("button", { name: "版本与更新" }));

    expect(await screen.findByText("暂无已发布的更新说明。")).toBeVisible();
  });

  it("shows an error state when release notes fail to load", async () => {
    listPublishedReleaseNotesMock.mockRejectedValue(new Error("network"));
    render(<SettingsDialog {...baseProps} />);
    fireEvent.click(screen.getByRole("button", { name: "版本与更新" }));

    expect(await screen.findByText("无法读取更新说明，请稍后重试。")).toBeVisible();
    expect(screen.queryByText("暂无已发布的更新说明。")).not.toBeInTheDocument();
  });

  it("retries loading release notes after the failure recovers", async () => {
    listPublishedReleaseNotesMock
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce({
        items: [{ id: "n1", version: "1.0.0", released_at: "2026-08-01T00:00:00", notes: ["新增发布说明功能"], status: "published" }],
      });
    render(<SettingsDialog {...baseProps} />);
    fireEvent.click(screen.getByRole("button", { name: "版本与更新" }));
    expect(await screen.findByText("无法读取更新说明，请稍后重试。")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));

    expect(await screen.findByText("v1.0.0")).toBeVisible();
    expect(listPublishedReleaseNotesMock).toHaveBeenCalledTimes(2);
  });

  it("persists submitted feedback before reporting success", async () => {
    render(<SettingsDialog {...baseProps} />);
    fireEvent.click(screen.getByRole("button", { name: "意见反馈" }));
    fireEvent.change(screen.getByPlaceholderText(/我希望/), { target: { value: "请增加错题计划" } });
    fireEvent.click(screen.getByRole("button", { name: "发布意见" }));

    await waitFor(() => expect(loadFeedback().map((item) => item.content)).toEqual(["请增加错题计划"]));
    expect(screen.getByText("意见已发送到开发者工作台。")).toBeVisible();
    expect(submitFeedbackMock).toHaveBeenCalledWith("请增加错题计划", "other");
  });

  it("shows an error card and keeps the draft when submission fails", async () => {
    submitFeedbackMock.mockRejectedValue(new Error("HTTP 403"));
    render(<SettingsDialog {...baseProps} />);
    fireEvent.click(screen.getByRole("button", { name: "意见反馈" }));
    fireEvent.change(screen.getByPlaceholderText(/我希望/), { target: { value: "会失败的意见" } });
    fireEvent.click(screen.getByRole("button", { name: "发布意见" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("发送失败：HTTP 403");
    expect(screen.getByPlaceholderText(/我希望/)).toHaveValue("会失败的意见");
    expect(screen.getByRole("button", { name: "发布意见" })).toBeEnabled();
    expect(loadFeedback()).toEqual([]);
    expect(submitFeedbackMock).toHaveBeenCalledWith("会失败的意见", "other");
  });

  it("loads older feedback history on demand", async () => {
    const baseThread = {
      thread_id: "thread-1",
      user_id: "u1",
      username: "student",
      display_name: "Student",
      status: "open" as const,
      category: "other" as const,
      priority: "medium" as const,
      updated_at: "2026-08-31T00:00:00+00:00",
    };
    getOwnFeedbackMock
      .mockResolvedValueOnce({
        ...baseThread,
        messages: [
          { id: "m2", sender_type: "developer", body: "回复", created_at: "2026-08-31T00:01:00+00:00" },
          { id: "m3", sender_type: "student", body: "追问", created_at: "2026-08-31T00:02:00+00:00" },
        ],
        message_total: 3,
        message_has_more: true,
        student_unread_count: 1,
      })
      .mockResolvedValueOnce({
        ...baseThread,
        messages: [{ id: "m1", sender_type: "student", body: "最初意见", created_at: "2026-08-31T00:00:00+00:00" }],
        message_total: 3,
        message_has_more: false,
    });
    render(<SettingsDialog {...baseProps} />);
    expect(await screen.findByLabelText("未读消息")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /意见反馈/ }));

    expect(await screen.findByRole("button", { name: "展开消息" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "展开消息" }));
    expect(await screen.findByText("回复")).toBeVisible();
    await waitFor(() => expect(markOwnFeedbackReadMock).toHaveBeenCalledOnce());
    expect(screen.queryByText("最初意见")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /加载更早反馈/ }));
    expect(await screen.findByText("最初意见")).toBeVisible();
    expect(getOwnFeedbackMock).toHaveBeenLastCalledWith({ limit: 50, offset: 2 });
  });

  it("disables the feedback entry for guests instead of rendering a doomed form", () => {
    render(<SettingsDialog {...baseProps} roles={["guest"]} />);

    const entry = screen.getByRole("button", { name: "意见反馈" });
    expect(entry).toBeDisabled();
    expect(entry).toHaveAttribute("title", "当前身份不支持提交反馈");
  });

  it("keeps feedback submittable for a custom role granted the permission", async () => {
    render(<SettingsDialog {...baseProps} roles={[]} permissions={["learning:feedback:submit"]} />);
    fireEvent.click(screen.getByRole("button", { name: "意见反馈" }));
    fireEvent.change(screen.getByPlaceholderText(/我希望/), { target: { value: "自定义角色也能提交" } });
    fireEvent.click(screen.getByRole("button", { name: "发布意见" }));

    await waitFor(() => expect(submitFeedbackMock).toHaveBeenCalledWith("自定义角色也能提交", "other"));
  });

  it("defers to server permissions even when built-in roles suggest otherwise", () => {
    render(<SettingsDialog {...baseProps} roles={["student"]} permissions={["identity:profile:read_self"]} />);

    expect(screen.getByRole("button", { name: "意见反馈" })).toBeDisabled();
  });

  it("only projects teacher and developer navigation for the matching roles", () => {
    const { rerender } = render(<SettingsDialog {...baseProps} roles={["student"]} />);

    expect(screen.queryByRole("button", { name: /进入教师模式/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /开发者工作台/ })).not.toBeInTheDocument();

    rerender(<SettingsDialog {...baseProps} roles={["teacher", "developer"]} />);
    expect(screen.getByRole("button", { name: /进入教师模式/ })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "数据与隐私" }));
    expect(screen.getByRole("button", { name: /开发者工作台/ })).toBeVisible();
  });
  it("updates the answer content font size", () => {
    const onChange = vi.fn();
    render(<SettingsDialog {...baseProps} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: /外观/ }));
    fireEvent.change(
      screen.getByRole("combobox", { name: /回答内容字号/ }),
      { target: { value: "large" } },
    );

    expect(onChange).toHaveBeenCalledWith({ content_font_size: "large" });
  });
    it("updates the reduce motion preference", () => {
    const onChange = vi.fn();
    render(<SettingsDialog {...baseProps} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: /外观/ }));
    fireEvent.click(
      screen.getByRole("checkbox", { name: /减少动态效果/ }),
    );

    expect(onChange).toHaveBeenCalledWith({ reduce_motion: true });
  });
    it("resets preferences after confirmation", () => {
    const onReset = vi.fn();
    render(<SettingsDialog {...baseProps} onReset={onReset} />);

    fireEvent.click(
      screen.getByRole("button", { name: "恢复默认" }),
    );

    const dialog = screen.getByRole("alertdialog", {
      name: "恢复默认偏好？",
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "恢复默认" }),
    );

    expect(onReset).toHaveBeenCalledOnce();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });
});
