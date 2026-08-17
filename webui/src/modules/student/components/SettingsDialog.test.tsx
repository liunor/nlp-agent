import { fireEvent, render, screen } from "@testing-library/react";

import { SettingsDialog } from "./SettingsDialog";
import { loadFeedback } from "@/shared/utils/feedback";
import { APP_VERSION } from "@/shared/version";
import type { UserSettings } from "@/shared/types";

const { listPublishedReleaseNotesMock } = vi.hoisted(() => ({ listPublishedReleaseNotesMock: vi.fn() }));
vi.mock("@/platform/http/api", () => ({ api: { listPublishedReleaseNotes: listPublishedReleaseNotesMock } }));

const settings: UserSettings = {
  theme: "system",
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
  onClose: () => {},
  onChange: () => {},
  onLearningContextChange: () => {},
  onOpenDeveloper: () => {},
  onOpenTeacher: () => {},
};

describe("SettingsDialog", () => {
  beforeEach(() => {
    localStorage.clear();
    listPublishedReleaseNotesMock.mockReset();
    listPublishedReleaseNotesMock.mockResolvedValue({ items: [] });
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

  it("persists submitted feedback before reporting success", () => {
    render(<SettingsDialog {...baseProps} />);
    fireEvent.click(screen.getByRole("button", { name: "意见反馈" }));
    fireEvent.change(screen.getByPlaceholderText(/我希望/), { target: { value: "请增加错题计划" } });
    fireEvent.click(screen.getByRole("button", { name: "发布意见" }));

    expect(loadFeedback().map((item) => item.content)).toEqual(["请增加错题计划"]);
    expect(screen.getByText("已将本次意见保存在此浏览器。")).toBeVisible();
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
});
