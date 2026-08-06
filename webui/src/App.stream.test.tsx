import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

const stream = vi.hoisted(() => {
  let onEvent: ((event: Record<string, unknown>) => void) | undefined;
  let lastRequestId = "";
  let lastModelProfile = "";
  const updateSettings = vi.fn(async (patch: Record<string, unknown>) => ({ settings: {
    locale: "zh-CN", theme: "system", show_reasoning: false,
    stream_render_interval_ms: 30, model_profile: "deepseek", ...patch,
  } }));
  return {
    lastRequestId: () => lastRequestId,
    lastModelProfile: () => lastModelProfile,
    updateSettings,
    emit(event: Record<string, unknown>) { onEvent?.(event); },
    StudentSocket: class {
      constructor(event: (value: Record<string, unknown>) => void, private readonly onStatus: (status: "connected") => void) { onEvent = event; }
      connect() { this.onStatus("connected"); }
      close() {}
      setSession() {}
      sendChat(_sessionId: string, _content: string, requestId: string, _learningContext?: object, modelProfile?: string) { lastRequestId = requestId; lastModelProfile = modelProfile ?? ""; }
      resume() {}
      cancel() {}
    },
  };
});

vi.mock("@/platform/realtime/client", () => ({ StudentSocket: stream.StudentSocket }));
vi.mock("@/platform/http/api", () => ({
  ensureAuth: vi.fn().mockResolvedValue({}),
  api: {
    getMe: vi.fn().mockResolvedValue({ user_id: "user", username: "user", display_name: "User", status: "active", roles: ["student"], workspace_ids: ["default"], permissions: [], created_at: "", updated_at: "" }),
    getAuthSession: vi.fn().mockResolvedValue({}),
    listSessions: vi.fn().mockResolvedValue({ items: [] }),
    getSettings: vi.fn().mockResolvedValue({
      preferences: { settings: {} },
      runtime: {
        default_model_profile: "deepseek",
        model_profiles: {
          deepseek: { label: "DeepSeek", provider: "deepseek", available: true },
          qwen: { label: "Qwen", provider: "dashscope", available: true },
        },
      },
    }),
    getLearningCatalog: vi.fn().mockResolvedValue({ catalog: { topics: [] } }),
    createSession: vi.fn().mockResolvedValue({ session_id: "session_1", user_id: "user", workspace_id: "default", channel: "web" }),
    listTurns: vi.fn().mockResolvedValue({ items: [] }),
    deleteSession: vi.fn(), updateSettings: stream.updateSettings,
  },
}));

import { App } from "./App";

const event = (type: string, payload: Record<string, unknown> = {}, timestamp = "2026-07-19T00:00:00Z") => ({ v: "1", type, timestamp, session_id: "session_1", turn_id: "turn_1", payload });

describe("student stream rendering", () => {
  it("keeps the wide school logo in the header flow and the theme control clear of learning records", async () => {
    render(<App />);

    const logo = await screen.findByAltText("学校校徽");
    expect(logo.closest(".thread-header-actions")).toBeVisible();
    expect(logo.closest(".thread-header")).toBeVisible();
    expect(screen.getByRole("button", { name: "切换主题" }).closest(".student-theme-control")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "学习记录" }));
    expect(document.querySelector(".learning-panel.open")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "切换主题" })).toBeInTheDocument();
  });

  it("selects and saves Qwen for subsequent chat sends", async () => {
    stream.updateSettings.mockClear();
    render(<App />);
    const modelSelect = await screen.findByRole("combobox", { name: "选择模型" });

    fireEvent.change(modelSelect, { target: { value: "qwen" } });
    await waitFor(() => expect(stream.updateSettings).toHaveBeenCalledWith({ model_profile: "qwen" }));

    const input = screen.getByRole("textbox", { name: "学习问题" });
    fireEvent.change(input, { target: { value: "解释 Qwen" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(stream.lastModelProfile()).toBe("qwen"));
  });

  it("shows a teaching configuration error returned by the Gateway", async () => {
    render(<App />);
    const input = await screen.findByRole("textbox", { name: "学习问题" });
    fireEvent.change(input, { target: { value: "开始练习" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    act(() => stream.emit({ ...event("command.error", { code: "teaching_configuration_error", message: "该主题尚未配置练习蓝图。" }), request_id: stream.lastRequestId() }));

    expect(await screen.findByRole("alert")).toHaveTextContent("该主题尚未配置练习蓝图。");
  });

  it("keeps the chat page mounted through real tool, worker and text stream events", async () => {
    render(<App />);
    const input = await screen.findByRole("textbox", { name: "学习问题" });
    fireEvent.change(input, { target: { value: "解释 Attention" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(screen.getAllByText("解释 Attention").some((node) => node.classList.contains("user-message"))).toBe(true));

    act(() => {
      stream.emit(event("command.ack", { accepted: true }));
      stream.emit(event("chat.started"));
      stream.emit(event("tool.started", { name: "search" }, "2026-07-19T00:00:01Z"));
      stream.emit(event("tool.progress", { name: "search", detail: "query" }, "2026-07-19T00:00:02Z"));
      stream.emit(event("worker.progress", { status: "running" }));
      stream.emit(event("chat.delta", { delta: "Attention 会计算相关性。" }));
      stream.emit(event("tool.completed", { name: "search" }, "2026-07-19T00:00:03Z"));
      stream.emit(event("worker.completed", { status: "completed" }));
      stream.emit(event("chat.completed", { content: "Attention 会计算相关性。" }, "2026-07-19T00:00:05Z"));
    });

    expect((await screen.findAllByText("Attention 会计算相关性。")).length).toBeGreaterThan(0);
    const activityTrigger = screen.getByRole("button", { name: /已处理 5s/ });
    expect(activityTrigger).toBeVisible();
    fireEvent.click(activityTrigger);
    expect(await screen.findByText("工具调用完成")).toBeVisible();
    expect(screen.getByText("search")).toBeVisible();
    expect(screen.queryByText("页面未能正常显示")).not.toBeInTheDocument();
  });
});
