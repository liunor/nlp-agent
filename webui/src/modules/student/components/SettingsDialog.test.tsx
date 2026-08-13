import { fireEvent, render, screen } from "@testing-library/react";

import { SettingsDialog } from "./SettingsDialog";
import { loadFeedback } from "@/shared/utils/feedback";
import type { UserSettings } from "@/shared/types";

const settings: UserSettings = {
  theme: "system",
  locale: "zh-CN",
  show_reasoning: true,
  stream_render_interval_ms: 30,
  model_profile: "deepseek",
  default_workspace_id: "default",
};

describe("SettingsDialog", () => {
  beforeEach(() => localStorage.clear());

  it("persists submitted feedback before reporting success", () => {
    render(<SettingsDialog
      open
      settings={settings}
      learningContext={{ topic_id: null, topic_name: "", level: "beginner", mode: "explain" }}
      onClose={() => {}}
      onChange={() => {}}
      onLearningContextChange={() => {}}
      onOpenDeveloper={() => {}}
      onOpenTeacher={() => {}}
      onOpenAdmin={() => {}}
    />);
    fireEvent.click(screen.getByRole("button", { name: "意见反馈" }));
    fireEvent.change(screen.getByPlaceholderText(/我希望/), { target: { value: "请增加错题计划" } });
    fireEvent.click(screen.getByRole("button", { name: "发布意见" }));

    expect(loadFeedback().map((item) => item.content)).toEqual(["请增加错题计划"]);
    expect(screen.getByText("已将本次意见保存在此浏览器。")).toBeVisible();
  });

  it("only projects teacher and developer navigation for the matching roles", () => {
    const props = {
      open: true,
      settings,
      learningContext: { topic_id: null, topic_name: "", level: "beginner" as const, mode: "explain" as const },
      onClose: () => {},
      onChange: () => {},
      onLearningContextChange: () => {},
      onOpenDeveloper: () => {},
      onOpenTeacher: () => {},
      onOpenAdmin: () => {},
    };
    const { rerender } = render(<SettingsDialog {...props} roles={["student"]} />);

    expect(screen.queryByRole("button", { name: /进入教师模式/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /开发者工作台/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /管理员模式/ })).not.toBeInTheDocument();

    rerender(<SettingsDialog {...props} roles={["teacher", "developer"]} />);
    expect(screen.getByRole("button", { name: /进入教师模式/ })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "数据与隐私" }));
    expect(screen.getByRole("button", { name: /开发者工作台/ })).toBeVisible();

    rerender(<SettingsDialog {...props} roles={["admin"]} />);
    fireEvent.click(screen.getByRole("button", { name: "数据与隐私" }));
    expect(screen.getByRole("button", { name: /管理员模式/ })).toBeVisible();
  });
});
