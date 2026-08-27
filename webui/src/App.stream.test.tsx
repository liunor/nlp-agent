import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

const stream = vi.hoisted(() => {
  let onEvent: ((event: Record<string, unknown>) => void) | undefined;
  let lastRequestId = "";
  let lastModelProfile = "";
  let lastMessage = "";
  const listTurns = vi.fn().mockResolvedValue({ items: [] });
  const uploadAttachment = vi.fn(async (sessionId: string, file: File) => ({
    file_name: "safe-image.png",
    url: `/api/v1/uploads/${sessionId}/safe-image.png`,
    media_type: file.type,
    size_bytes: file.size,
    width: 120,
    height: 80,
    sha256: "a".repeat(64),
  }));
  const updateSettings = vi.fn(async (patch: Record<string, unknown>) => ({ settings: {
    locale: "zh-CN", theme: "system",content_font_size: "medium", reduce_motion: false, show_reasoning: false,
    stream_render_interval_ms: 30, model_profile: "deepseek", ...patch,
  } }));
  const ensureSandboxLease = vi.fn(async () => ({
    phase: 0,
    runtime_available: true,
    runtime: { kind: "inmemory" as const, ticket: null },
    environment: { id: "sandbox-user", status: "ready", generation: 1, profile: "python-base" },
    lease: { id: "lease-session", state: "active", generation: 1, expires_at: "2026-08-25T10:00:00" },
  }));
  const executeSandbox = vi.fn(async () => ({ status: "completed", stdout: "2\n", stderr: "" }));
  return {
    lastRequestId: () => lastRequestId,
    lastModelProfile: () => lastModelProfile,
    lastMessage: () => lastMessage,
    listTurns,
    uploadAttachment,
    updateSettings,
    ensureSandboxLease,
    executeSandbox,
    emit(event: Record<string, unknown>) { onEvent?.(event); },
    StudentSocket: class {
      constructor(event: (value: Record<string, unknown>) => void, private readonly onStatus: (status: "connected") => void) { onEvent = event; }
      connect() { this.onStatus("connected"); }
      close() {}
      setSession() {}
      sendChat(_sessionId: string, content: string, requestId: string, _learningContext?: object, modelProfile?: string) { lastRequestId = requestId; lastModelProfile = modelProfile ?? ""; lastMessage = content; }
      resume() {}
      cancel() {}
    },
  };
});

vi.mock("@/platform/realtime/client", () => ({ StudentSocket: stream.StudentSocket }));
vi.mock("@/platform/http/api", () => ({
  ensureAuth: vi.fn().mockResolvedValue({}),
  uploadAttachment: stream.uploadAttachment,
  api: {
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
    listTurns: stream.listTurns,
    deleteSession: vi.fn(), updateSettings: stream.updateSettings,
    ensureSandboxLease: stream.ensureSandboxLease,
    executeSandbox: stream.executeSandbox,
  },
}));

import { App } from "./App";

const event = (type: string, payload: Record<string, unknown> = {}, timestamp = "2026-07-19T00:00:00Z") => ({ v: "1", type, timestamp, session_id: "session_1", turn_id: "turn_1", payload });

describe("student stream rendering", () => {
  it("uses a Codex-style tools dock while keeping theme mode in the fixed header actions and the school logo at the upper left", async () => {
    render(<App />);

    const logo = await screen.findByAltText("学校校徽");
    expect(logo.closest(".student-school-logo")).toBeVisible();
    expect(document.querySelector(".student-app-shell")).toHaveClass("sidebar-is-collapsed");
    expect(logo.closest(".thread-header")).toBeNull();
    expect(screen.getByRole("button", { name: "切换主题" }).closest(".thread-header-actions")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "打开工具侧栏" }));
    expect(screen.getByRole("button", { name: "打开学习记录工具" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "显示工具列表" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "打开学习记录工具" }));
    expect(screen.getByRole("tab", { name: "学习记录" })).toBeVisible();
    expect(document.querySelector(".tool-dock .learning-panel")).toBeInTheDocument();
  });

  it("selects and saves Qwen for subsequent chat sends", async () => {
    stream.updateSettings.mockClear();
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "学习设置" }));
    fireEvent.click(screen.getByRole("button", { name: "对话模型" }));

    fireEvent.click(screen.getByRole("option", { name: "Qwen" }));
    await waitFor(() => expect(stream.updateSettings).toHaveBeenCalledWith({ model_profile: "qwen" }));

    const input = screen.getByRole("textbox", { name: "学习问题" });
    fireEvent.change(input, { target: { value: "解释 Qwen" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(stream.lastModelProfile()).toBe("qwen"));
  });

  it("creates the deferred session when the first action is an image upload", async () => {
    stream.listTurns.mockClear();
    stream.uploadAttachment.mockClear();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:first-image-preview"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    render(<App />);
    const uploadButton = await screen.findByRole("button", { name: "上传附件" });
    const file = new File(["image"], "first-image.png", { type: "image/png" });

    expect(uploadButton).toBeEnabled();
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [file] } });

    await waitFor(() => expect(stream.uploadAttachment).toHaveBeenCalledWith("session_1", file));
    expect(screen.getByRole("img", { name: "first-image.png" })).toBeVisible();
    expect(stream.listTurns).not.toHaveBeenCalled();
  });

  it("keeps every available tool as a closable tab in the right workbench dock", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "打开工具侧栏" }));
    fireEvent.click(screen.getByRole("button", { name: "打开文件工具" }));
    expect(screen.getByRole("tab", { name: "文件" })).toBeVisible();

    for (const tool of ["打开学习记录工具", "打开浏览器工具", "打开终端工具", "打开代码沙箱工具"]) {
      fireEvent.click(screen.getByRole("button", { name: "显示工具列表" }));
      fireEvent.click(screen.getByRole("menuitem", { name: tool }));
    }

    expect(screen.getByRole("tab", { name: "文件" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "学习记录" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "浏览器" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "终端" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "代码沙箱" })).toBeVisible();
  });

  it("opens the Phase 0 code sandbox as a first-class right-workbench page", async () => {
    stream.ensureSandboxLease.mockClear();
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "打开工具侧栏" }));
    fireEvent.click(screen.getByRole("button", { name: "打开代码沙箱工具" }));

    expect(screen.getByRole("tab", { name: "代码沙箱" })).toBeVisible();
    expect(screen.getByText("Code Runner")).toBeVisible();
    expect(screen.getByText(/当前会话使用隔离运行环境/)).toBeVisible();
    await waitFor(() => expect(stream.ensureSandboxLease).toHaveBeenCalledTimes(1));
  });

  it("keeps the kernel status in the environment strip when the workbench is expanded", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "打开工具侧栏" }));
    fireEvent.click(screen.getByRole("button", { name: "打开代码沙箱工具" }));
    const editor = screen.getByRole("region", { name: "代码工作台" });
    const status = editor.querySelector(".sandbox-runtime-status");
    expect(status).not.toBeNull();
    expect(status!.closest(".sandbox-environment-bar")).not.toBeNull();
    expect(status!.closest(".sandbox-workbench-titlebar")).toBeNull();
  });

  it("runs code from the sandbox workbench and renders stdout", async () => {
    stream.executeSandbox.mockClear();
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "打开工具侧栏" }));
    fireEvent.click(screen.getByRole("button", { name: "打开代码沙箱工具" }));
    fireEvent.change(screen.getByRole("textbox", { name: "沙箱代码" }), { target: { value: "x = 1\nx + 1" } });
    fireEvent.click(screen.getByRole("button", { name: "运行代码" }));
    await waitFor(() => expect(stream.executeSandbox).toHaveBeenCalledWith("x = 1\nx + 1", null));
    await waitFor(() => expect(screen.getByRole("region", { name: "运行输出" })).toHaveTextContent("2"));
  });

  it("opens the sandbox as a normally sized light editor with concise theme labels", async () => {
    window.localStorage.removeItem("nova.sandbox.editor-theme");
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "打开工具侧栏" }));
    fireEvent.click(screen.getByRole("button", { name: "打开代码沙箱工具" }));

    const editor = screen.getByRole("region", { name: "代码工作台" });
    expect(editor).toHaveAttribute("data-editor-theme", "light");
    expect(screen.getByRole("list", { name: "代码行号" })).toHaveTextContent("1");
    expect(screen.getByRole("separator", { name: "调整输出面板高度" })).toBeVisible();
    expect(screen.getByRole("button", { name: "浅色" })).toBeVisible();
    expect(screen.getByRole("button", { name: "深色" })).toBeVisible();
    expect(document.querySelector(".app-shell")).not.toHaveClass("tool-dock-expanded");
    expect(screen.getByRole("button", { name: "展开工具面板" })).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "深色" }));
    expect(editor).toHaveAttribute("data-editor-theme", "dark");
  });

  it("retains the editor theme after the sandbox page is replaced", async () => {
    window.localStorage.removeItem("nova.sandbox.editor-theme");
    const first = render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "打开工具侧栏" }));
    fireEvent.click(screen.getByRole("button", { name: "打开代码沙箱工具" }));
    fireEvent.click(screen.getByRole("button", { name: "深色" }));
    expect(screen.getByRole("region", { name: "代码工作台" })).toHaveAttribute("data-editor-theme", "dark");
    first.unmount();

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "打开工具侧栏" }));
    fireEvent.click(screen.getByRole("button", { name: "打开代码沙箱工具" }));
    expect(screen.getByRole("region", { name: "代码工作台" })).toHaveAttribute("data-editor-theme", "dark");
  });

  it("supports editor-only font zoom, syntax tokens, reset and keyboard shortcuts", async () => {
    stream.executeSandbox.mockClear();
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "打开工具侧栏" }));
    fireEvent.click(screen.getByRole("button", { name: "打开代码沙箱工具" }));
    const editor = screen.getByRole("region", { name: "代码工作台" });
    const input = screen.getByRole("textbox", { name: "沙箱代码" }) as HTMLTextAreaElement;

    fireEvent.change(input, { target: { value: "def hello(name):\n    return f'Hi {name}'" } });
    expect(editor.querySelector(".sandbox-syntax-keyword")).toHaveTextContent("def");
    fireEvent.click(screen.getByRole("button", { name: "放大代码字体" }));
    expect(editor.style.getPropertyValue("--sandbox-editor-font-size")).toBe("16px");

    input.setSelectionRange(0, input.value.length);
    fireEvent.keyDown(input, { key: "/", ctrlKey: true });
    expect(input).toHaveValue("# def hello(name):\n    # return f'Hi {name}'");
    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(stream.executeSandbox).toHaveBeenCalledWith("# def hello(name):\n    # return f'Hi {name}'", null));

    fireEvent.click(screen.getByRole("button", { name: "清空代码" }));
    expect(input).toHaveValue("");
  });

  it("offers the current source as a browser download", async () => {
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "打开工具侧栏" }));
    fireEvent.click(screen.getByRole("button", { name: "打开代码沙箱工具" }));
    fireEvent.change(screen.getByRole("textbox", { name: "沙箱代码" }), { target: { value: "print('download')" } });
    fireEvent.click(screen.getByRole("button", { name: "下载代码" }));

    expect(anchorClick).toHaveBeenCalledTimes(1);
    anchorClick.mockRestore();
  });

  it("copies the editor source and sends it to the main agent for explanation", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "打开工具侧栏" }));
    fireEvent.click(screen.getByRole("button", { name: "打开代码沙箱工具" }));
    fireEvent.change(screen.getByRole("textbox", { name: "沙箱代码" }), { target: { value: "total = sum(range(5))" } });
    fireEvent.click(screen.getByRole("button", { name: "复制代码" }));
    expect(writeText).toHaveBeenCalledWith("total = sum(range(5))");

    fireEvent.click(screen.getByRole("button", { name: "解释此代码" }));
    await waitFor(() => expect(stream.lastMessage()).toBe("请解释以下 Python 代码：\n\n```python\ntotal = sum(range(5))\n```"));
  });

  it("opens the tool picker from the plus trigger and closes it with the dock", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "打开工具侧栏" }));
    fireEvent.click(screen.getByRole("button", { name: "打开文件工具" }));
    fireEvent.click(screen.getByRole("button", { name: "显示工具列表" }));

    const menu = screen.getByRole("menu", { name: "工具列表" });
    expect(menu).toBeVisible();
    expect(document.querySelector(".tool-dock-tab-strip")).toBeInTheDocument();
    expect(menu.closest(".tool-dock-tab-strip")).toBeNull();
    expect(screen.getByRole("menuitem", { name: "打开浏览器工具" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "文件" })).toBeVisible();
    expect(screen.getByRole("button", { name: "显示工具列表" }).parentElement).toContainElement(menu);

    fireEvent.click(screen.getByRole("button", { name: "关闭工具侧栏" }));
    expect(screen.queryByRole("menu", { name: "工具列表" })).not.toBeInTheDocument();
  });

  it("exposes a wide draggable dock separator and a full workbench mode", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "打开工具侧栏" }));
    fireEvent.click(screen.getByRole("button", { name: "打开文件工具" }));

    const separator = screen.getByRole("separator", { name: "调整工具侧栏宽度" });
    expect(separator).toHaveAttribute("aria-valuenow", "420");
    const maxWidth = Number(separator.getAttribute("aria-valuemax"));
    expect(maxWidth).toBe(window.innerWidth - 560);
    fireEvent.pointerDown(separator, { clientX: 480 });
    fireEvent.pointerMove(window, { clientX: 360 });
    fireEvent.pointerUp(window);
    expect(Number(separator.getAttribute("aria-valuenow"))).toBe(maxWidth);

    fireEvent.click(screen.getByRole("button", { name: "展开工具面板" }));
    expect(document.querySelector(".app-shell")).toHaveClass("tool-dock-expanded");
    expect(screen.getByRole("button", { name: "还原工具面板" })).toBeVisible();
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
    expect(await screen.findByRole("button", { name: "学习设置" })).toBeVisible();
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
