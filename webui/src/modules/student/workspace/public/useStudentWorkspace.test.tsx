import { act, render, renderHook, waitFor } from "@testing-library/react";
import type { ServerEvent, SessionSummary, TurnRecord } from "@/shared/types";
import { useStudentWorkspace } from "./useStudentWorkspace";
import { api, AUTH_EXPIRED_EVENT } from "@/platform/http/api";
import { AuthProvider } from "@/platform/auth/AuthContext";
import type { SettingsRuntime } from "@/shared/types";

const runtime: SettingsRuntime = {
  default_model_profile: "deepseek",
  model_profiles: {
    deepseek: { label: "DeepSeek", provider: "deepseek", available: true, supported_levels: ["quick", "light", "deep", "extreme"] },
    qwen: { label: "Qwen", provider: "dashscope", available: true },
  },
};

const {
  ensureAuthMock,
  getSettingsMock,
  createSessionMock,
  cancelTurnMock,
  deleteSessionMock,
  listTurnsMock,
  sendChatMock,
  renameSessionMock,
  socketConstructMock,
  socketConnectMock,
  socketCloseMock,
  socketEventHandlerRef,
  socketCancelMock,
} = vi.hoisted(() => ({
  ensureAuthMock: vi.fn(),
  getSettingsMock: vi.fn(),
  createSessionMock: vi.fn(),
  cancelTurnMock: vi.fn(),
  deleteSessionMock: vi.fn(async () => undefined),
  listTurnsMock: vi.fn(),
  sendChatMock: vi.fn(),
  renameSessionMock: vi.fn(),
  socketConnectMock: vi.fn(),
  socketCloseMock: vi.fn(),
  socketEventHandlerRef: {
    current: undefined as ((event: ServerEvent) => void) | undefined,
  },
  socketCancelMock: vi.fn(),
  socketConstructMock: vi.fn(),
}));

vi.mock("@/platform/http/api", () => ({
  AUTH_EXPIRED_EVENT: "nova:auth-expired",
  AuthStateChangedError: class extends Error {},
  getAuthGeneration: () => 0,
  subscribeAuthChanges: () => () => undefined,
  ensureAuth: ensureAuthMock,
  api: {
    listSessions: vi.fn(async () => ({ items: [] })),
    getSettings: getSettingsMock,
    createSession: createSessionMock,
    cancelTurn: cancelTurnMock,
    deleteSession: deleteSessionMock,
    listTurns: listTurnsMock,
    renameSession: renameSessionMock,
    login: vi.fn(),
    logout: vi.fn(async () => undefined),
    updateSettings: vi.fn(),
  },
}));

vi.mock("@/platform/realtime/client", () => ({
  StudentSocket: class {
    constructor(handler: (event: ServerEvent) => void, ...args: unknown[]) {
      socketConstructMock(handler, ...args);
      socketEventHandlerRef.current = handler;
    }

    connect() { socketConnectMock(); }
    close() { socketCloseMock(); }
    setSession() {}
    sendChat(...args: unknown[]) { sendChatMock(...args); }
    cancel(...args: unknown[]) { socketCancelMock(...args); }
  },
}));

describe("useStudentWorkspace settings", () => {
  let dark = false;
  let onChange: (() => void) | undefined;

  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    dark = false;
    onChange = undefined;
    socketConnectMock.mockReset();
    socketCloseMock.mockReset();
    socketEventHandlerRef.current = undefined;
    document.documentElement.classList.remove("dark");
    ensureAuthMock.mockResolvedValue({ csrf_token: "x", workspace_ids: ["default"] });
    getSettingsMock.mockResolvedValue({ preferences: { settings: { theme: "system" } }, runtime });
    createSessionMock.mockResolvedValue({ session_id: "session-new", user_id: "user", workspace_id: "default", channel: "web" });
    vi.stubGlobal("matchMedia", vi.fn(() => ({
      get matches() { return dark; },
      addEventListener: (_type: string, listener: () => void) => { onChange = listener; },
      removeEventListener: vi.fn(),
    })));
    vi.mocked(api.updateSettings).mockReset();
    vi.mocked(api.logout).mockReset();
    vi.mocked(api.listSessions).mockResolvedValue({ items: [] });
    listTurnsMock.mockResolvedValue({ items: [] });
    createSessionMock.mockClear();
    cancelTurnMock.mockReset();
    cancelTurnMock.mockResolvedValue({ status: "cancelled" });
    deleteSessionMock.mockClear();
    sendChatMock.mockClear();
    socketCancelMock.mockClear();
    renameSessionMock.mockClear();
    socketConstructMock.mockClear();
  });

  it("restores a rejected question after changing sessions and remounting, and removes it only on request", async () => {
    ensureAuthMock.mockResolvedValue({ user_id: "owner-a", csrf_token: "x", workspace_ids: ["default"] });
    const first = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(first.result.current.bootStatus).toBe("ready"));
    act(() => first.result.current.selectSession("session-a"));
    await waitFor(() => expect(first.result.current.loadingMessages).toBe(false));
    await act(async () => { await first.result.current.send("保留的问题"); });
    const requestId = sendChatMock.mock.calls[0][2] as string;
    act(() => socketEventHandlerRef.current?.({ v: "1", type: "command.error", request_id: requestId,
      session_id: "session-a", timestamp: new Date().toISOString(), payload: { code: "quota_request_limit" } }));
    expect(first.result.current.messages).toEqual(expect.arrayContaining([expect.objectContaining({ content: "保留的问题", status: "failed" })]));
    act(() => first.result.current.selectSession("session-b"));
    await waitFor(() => expect(first.result.current.loadingMessages).toBe(false));
    expect(first.result.current.messages).toEqual([]);
    act(() => first.result.current.selectSession("session-a"));
    await waitFor(() => expect(first.result.current.messages).toEqual(expect.arrayContaining([expect.objectContaining({ content: "保留的问题", status: "failed" })])));
    first.unmount();
    const second = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(second.result.current.bootStatus).toBe("ready"));
    act(() => second.result.current.selectSession("session-a"));
    await waitFor(() => expect(second.result.current.messages).toEqual(expect.arrayContaining([expect.objectContaining({ content: "保留的问题", status: "failed" })])));
    const id = second.result.current.messages.find((item) => item.status === "failed")!.id;
    act(() => second.result.current.dismissRejected(id));
    act(() => second.result.current.selectSession("session-b"));
    act(() => second.result.current.selectSession("session-a"));
    await waitFor(() => expect(second.result.current.loadingMessages).toBe(false));
    expect(second.result.current.messages).toEqual([]);
  });

  it("keeps default WS tickets and refreshes auth before restoring history on ready", async () => {
    const wrapper = ({ children }: { children: React.ReactNode }) => <AuthProvider>{children}</AuthProvider>;
    const { result } = renderHook(() => useStudentWorkspace(), { wrapper });
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));
    act(() => result.current.selectSession("session-a"));
    await waitFor(() => expect(api.listTurns).toHaveBeenCalledWith("session-a"));
    ensureAuthMock.mockClear();
    vi.mocked(api.listTurns).mockClear();
    const args = socketConstructMock.mock.lastCall!;
    expect(args[2]).toBeUndefined();
    expect(args[3]).toBeTypeOf("function");
    await act(async () => { await args[3](); });
    expect(ensureAuthMock).toHaveBeenCalledTimes(1);
    expect(api.listTurns).toHaveBeenCalledWith("session-a");
    expect(ensureAuthMock.mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(api.listTurns).mock.invocationCallOrder[0]);
  });

  it("does not restore a delayed settings GET while logout is still pending", async () => {
    let finishSettings!: (value: Awaited<ReturnType<typeof api.getSettings>>) => void;
    let finishLogout!: () => void;
    getSettingsMock.mockReturnValueOnce(new Promise((resolve) => { finishSettings = resolve; }));
    vi.mocked(api.logout).mockReturnValueOnce(new Promise((resolve) => { finishLogout = resolve; }));
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(getSettingsMock).toHaveBeenCalled());
    let leaving!: Promise<void>;
    act(() => { leaving = result.current.logout(); });
    await act(async () => finishSettings({ preferences: { settings: { thinking_level: "extreme" } }, runtime }));
    expect(result.current.settings.thinking_level).toBe("light");
    await act(async () => { await result.current.patchSettings({ thinking_level: "deep" }); });
    expect(api.updateSettings).not.toHaveBeenCalled();
    await act(async () => { finishLogout(); await leaving; });
  });

  it("rolls back optimistic settings and exposes a visible error on network failure", async () => {
    vi.mocked(api.updateSettings).mockRejectedValueOnce(new Error("offline"));
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    await act(async () => { await result.current.patchSettings({ theme: "dark" }); });

    expect(result.current.settings.theme).toBe("system");
    expect(result.current.settingsError).toContain("offline");
  });

  it("blocks every send while the selected level is being saved and rolls back failure", async () => {
    let fail!: (reason: Error) => void;
    vi.mocked(api.updateSettings).mockReturnValueOnce(new Promise((_resolve, reject) => { fail = reject; }));
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));
    let save!: Promise<void>;
    await act(async () => {
      save = result.current.patchSettings({ thinking_level: "extreme" });
      await result.current.send("不能抢在保存前发送");
    });
    expect(sendChatMock).not.toHaveBeenCalled();
    expect(createSessionMock).not.toHaveBeenCalled();
    expect(result.current.selectionPending).toBe(true);
    await act(async () => { fail(new Error("offline")); await save; });
    expect(result.current.selectionPending).toBe(false);
    expect(result.current.settings.thinking_level).toBe("light");
    await act(async () => { await result.current.send("使用已恢复的等级"); });
    expect(sendChatMock.mock.calls[0][6]).toBe("light");
  });

  it("abandons a deferred first send when its captured level changes before session creation", async () => {
    let finishSession!: (session: { session_id: string; user_id: string; workspace_id: string; channel: string }) => void;
    createSessionMock.mockReturnValueOnce(new Promise((resolve) => { finishSession = resolve; }));
    vi.mocked(api.updateSettings).mockResolvedValueOnce({ settings: { thinking_level: "deep" } });
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));
    let sending!: Promise<void>;
    act(() => { sending = result.current.send("旧等级不应在稍后发送"); });
    await act(async () => { await result.current.patchSettings({ thinking_level: "deep" }); });
    await act(async () => { finishSession({ session_id: "session-new", user_id: "user", workspace_id: "default", channel: "web" }); await sending; });
    expect(sendChatMock).not.toHaveBeenCalled();
    expect(result.current.messages).toEqual([]);
  });

  it("rolls consecutive failed writes back to the last confirmed settings", async () => {
    vi.mocked(api.updateSettings)
      .mockRejectedValueOnce(new Error("first failed"))
      .mockRejectedValueOnce(new Error("second failed"));
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    await act(async () => {
      await Promise.all([
        result.current.patchSettings({ theme: "dark" }),
        result.current.patchSettings({ locale: "en-US" }),
      ]);
    });

    expect(result.current.settings.theme).toBe("system");
    expect(result.current.settings.locale).toBe("zh-CN");
    expect(result.current.settingsError).toContain("second failed");
  });

  it("preserves confirmed defaults when the backend returns only the changed settings", async () => {
    getSettingsMock.mockResolvedValue({
      preferences: {
        settings: {
          theme: "system",
          content_font_size: "large",
          reduce_motion: true,
          show_reasoning: false,
          stream_render_interval_ms: 80,
          model_profile: "qwen",
        },
      },
      runtime,
    });
    vi.mocked(api.updateSettings).mockResolvedValueOnce({ settings: { theme: "dark" } });
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    await act(async () => { await result.current.patchSettings({ theme: "dark" }); });

    expect(result.current.settings).toMatchObject({
      theme: "dark",
      content_font_size: "large",
      reduce_motion: true,
      show_reasoning: false,
      stream_render_interval_ms: 80,
      model_profile: "qwen",
    });
  });

  it("clears an earlier failure after a later queued setting is saved", async () => {
    vi.mocked(api.updateSettings)
      .mockRejectedValueOnce(new Error("first failed"))
      .mockResolvedValueOnce({ settings: {
        theme: "light", locale: "zh-CN", content_font_size: "medium", reduce_motion: false, show_reasoning: true,
        stream_render_interval_ms: 30, model_profile: "deepseek", default_workspace_id: "default",
      } });
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    await act(async () => {
      await Promise.all([
        result.current.patchSettings({ theme: "dark" }),
        result.current.patchSettings({ theme: "light" }),
      ]);
    });

    expect(result.current.settings.theme).toBe("light");
    expect(result.current.settingsError).toBe("");
  });

  it("loads backend model profiles and prefers the saved profile over the runtime default", async () => {
    getSettingsMock.mockResolvedValue({
      preferences: { settings: { theme: "system", model_profile: "qwen" } },
      runtime,
    });
    const { result } = renderHook(() => useStudentWorkspace());

    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    expect(result.current.settings.model_profile).toBe("qwen");
    expect(result.current.modelProfiles.qwen.label).toBe("Qwen");
  });

  it("uses the runtime default model when the user has not saved one", async () => {
    const { result } = renderHook(() => useStudentWorkspace());

    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    expect(result.current.settings.model_profile).toBe("deepseek");
  });

  it("tracks operating-system theme changes while using system mode", async () => {
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));
    expect(document.documentElement).not.toHaveClass("dark");

    dark = true;
    act(() => onChange?.());

    expect(document.documentElement).toHaveClass("dark");
  });

  it("uses the light theme by default instead of inheriting a dark operating-system preference", async () => {
    dark = true;
    getSettingsMock.mockResolvedValue({ preferences: { settings: {} }, runtime });

    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    expect(result.current.settings.theme).toBe("light");
    expect(document.documentElement).not.toHaveClass("dark");
  });

  it("resets the workspace to the light theme when leaving an authenticated session", async () => {
    getSettingsMock.mockResolvedValue({ preferences: { settings: { theme: "dark" } }, runtime });

    const wrapper = ({ children }: { children: React.ReactNode }) => <AuthProvider>{children}</AuthProvider>;
    const { result } = renderHook(() => useStudentWorkspace(), { wrapper });
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));
    expect(result.current.settings.theme).toBe("dark");
    expect(document.documentElement).toHaveClass("dark");

    await act(async () => { await result.current.logout(); });

    await waitFor(() => expect(result.current.bootStatus).toBe("unauthenticated"));
    expect(result.current.settings.theme).toBe("light");
    expect(document.documentElement).not.toHaveClass("dark");
  });

  it("removes browser-side metadata for sessions deleted by a monitor reset", async () => {
    localStorage.setItem("nlp-agent.learning-preferences.v1", JSON.stringify({
      version: 2,
      context: { topic_id: null, topic_name: "", level: "beginner", mode: "explain" },
      categories: [],
      sessions: { "deleted-session": { title: "旧对话", updatedAt: 1 } },
    }));
    const { result } = renderHook(() => useStudentWorkspace());

    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    expect(result.current.preferences.sessions).toEqual({});
    expect(JSON.parse(localStorage.getItem("nlp-agent.learning-preferences.v1") ?? "{}").sessions).toEqual({});
  });

  it("renames a session through the backend and drops stale local title metadata", async () => {
    vi.mocked(api.listSessions).mockResolvedValue({ items: [{ session_id: "session-new", user_id: "user", workspace_id: "default", channel: "web" }] });
    renameSessionMock.mockResolvedValue({ session_id: "session-new", title: "新标题" });
    localStorage.setItem("nlp-agent.learning-preferences.v1", JSON.stringify({
      version: 2,
      context: { topic_id: null, topic_name: "", level: "beginner", mode: "explain" },
      categories: [],
      sessions: { "session-new": { title: "旧标题", updatedAt: 1 } },
    }));
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    await act(async () => { await result.current.renameSessionTitle("session-new", "新标题"); });

    expect(renameSessionMock).toHaveBeenCalledWith("session-new", "新标题");
    expect(result.current.sessions[0].title).toBe("新标题");
    const stored = JSON.parse(localStorage.getItem("nlp-agent.learning-preferences.v1") ?? "{}").sessions["session-new"];
    expect(stored.title).toBeUndefined();
  });

  it("surfaces a rename failure instead of silently dropping it", async () => {
    vi.mocked(api.listSessions).mockResolvedValue({ items: [{ session_id: "session-new", user_id: "user", workspace_id: "default", channel: "web", title: "原标题" }] });
    renameSessionMock.mockRejectedValue(new Error("network down"));
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    await act(async () => { await result.current.renameSessionTitle("session-new", "新标题"); });

    expect(renameSessionMock).toHaveBeenCalledWith("session-new", "新标题");
    expect(result.current.requestError?.message).toContain("重命名失败");
    expect(result.current.sessions[0].title).toBe("原标题");
  });

  it("starts a new chat without creating a backend session until a message is sent", async () => {
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));
    const composerRevision = result.current.composerRevision;

    await act(async () => { await result.current.startNewChat(); });

    expect(createSessionMock).not.toHaveBeenCalled();
    expect(result.current.activeSessionId).toBeNull();
    expect(result.current.composerRevision).toBe(composerRevision + 1);
    expect(result.current.messages).toEqual([]);
    expect(result.current.loadingMessages).toBe(false);
  });

  it("changes the composer scope only when selecting a different conversation", async () => {
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));
    const composerRevision = result.current.composerRevision;

    act(() => result.current.selectSession("session-a"));

    expect(result.current.activeSessionId).toBe("session-a");
    expect(result.current.composerRevision).toBe(composerRevision + 1);

    act(() => result.current.selectSession("session-a"));
    expect(result.current.composerRevision).toBe(composerRevision + 1);
  });

  it("clears the previous conversation while the selected session history is loading", async () => {
    const turn = (sessionId: string, content: string): TurnRecord => ({
      turn_id: `${sessionId}-turn`,
      session_id: sessionId,
      status: "completed",
      input_text: `${sessionId} question`,
      final_text: content,
      error_kind: null,
      error_message: null,
      created_at: "2026-09-10T00:00:00Z",
      started_at: "2026-09-10T00:00:01Z",
      completed_at: "2026-09-10T00:00:02Z",
    });
    vi.mocked(api.listSessions).mockResolvedValue({ items: [
      { session_id: "session-a", user_id: "user", workspace_id: "default", channel: "web" },
      { session_id: "session-b", user_id: "user", workspace_id: "default", channel: "web" },
    ] });
    let resolveSessionB!: (response: { items: TurnRecord[] }) => void;
    listTurnsMock.mockImplementation(async (sessionId: string) => {
      if (sessionId === "session-a") return { items: [turn(sessionId, "会话 A 内容")] };
      return new Promise((resolve) => { resolveSessionB = resolve; });
    });
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    await act(async () => { result.current.selectSession("session-a"); });
    await waitFor(() => expect(result.current.messages.some((message) => message.content === "会话 A 内容")).toBe(true));

    await act(async () => {
      result.current.selectSession("session-b");
      await Promise.resolve();
    });

    expect(result.current.messages).toEqual([]);
    expect(result.current.loadingMessages).toBe(true);

    await act(async () => { resolveSessionB({ items: [turn("session-b", "会话 B 内容")] }); });
    await waitFor(() => expect(result.current.messages.some((message) => message.content === "会话 B 内容")).toBe(true));
  });

  it("never renders the previous conversation under a newly selected session", async () => {
    const turn = (sessionId: string, content: string): TurnRecord => ({
      turn_id: `${sessionId}-turn`,
      session_id: sessionId,
      status: "completed",
      input_text: `${sessionId} question`,
      final_text: content,
      error_kind: null,
      error_message: null,
      created_at: "2026-09-10T00:00:00Z",
      started_at: "2026-09-10T00:00:01Z",
      completed_at: "2026-09-10T00:00:02Z",
    });
    vi.mocked(api.listSessions).mockResolvedValue({ items: [
      { session_id: "session-a", user_id: "user", workspace_id: "default", channel: "web" },
      { session_id: "session-b", user_id: "user", workspace_id: "default", channel: "web" },
    ] });
    let resolveSessionB!: (response: { items: TurnRecord[] }) => void;
    listTurnsMock.mockImplementation(async (sessionId: string) => {
      if (sessionId === "session-a") return { items: [turn(sessionId, "会话 A 内容")] };
      return new Promise((resolve) => { resolveSessionB = resolve; });
    });
    const snapshots: Array<{ activeSessionId: string | null; messages: string[] }> = [];
    let workspace!: ReturnType<typeof useStudentWorkspace>;
    function Probe() {
      workspace = useStudentWorkspace();
      snapshots.push({
        activeSessionId: workspace.activeSessionId,
        messages: workspace.messages.map((message) => message.content),
      });
      return null;
    }
    const view = render(<Probe />);
    await waitFor(() => expect(workspace.bootStatus).toBe("ready"));

    await act(async () => { workspace.selectSession("session-a"); });
    await waitFor(() => expect(workspace.messages.some((message) => message.content === "会话 A 内容")).toBe(true));
    snapshots.length = 0;

    await act(async () => {
      workspace.selectSession("session-b");
      await Promise.resolve();
    });

    expect(snapshots).not.toContainEqual(expect.objectContaining({
      activeSessionId: "session-b",
      messages: expect.arrayContaining(["会话 A 内容"]),
    }));

    await act(async () => { resolveSessionB({ items: [turn("session-b", "会话 B 内容")] }); });
    view.unmount();
  });

  it("never renders the previous conversation after deleting the active session", async () => {
    const turn = (sessionId: string, content: string): TurnRecord => ({
      turn_id: `${sessionId}-turn`,
      session_id: sessionId,
      status: "completed",
      input_text: `${sessionId} question`,
      final_text: content,
      error_kind: null,
      error_message: null,
      created_at: "2026-09-10T00:00:00Z",
      started_at: "2026-09-10T00:00:01Z",
      completed_at: "2026-09-10T00:00:02Z",
    });
    vi.mocked(api.listSessions).mockResolvedValue({ items: [
      { session_id: "session-a", user_id: "user", workspace_id: "default", channel: "web" },
      { session_id: "session-b", user_id: "user", workspace_id: "default", channel: "web" },
    ] });
    listTurnsMock.mockImplementation(async (sessionId: string) => ({ items: [turn(sessionId, sessionId === "session-a" ? "会话 A 内容" : "会话 B 内容")] }));
    const snapshots: Array<{ activeSessionId: string | null; messages: string[] }> = [];
    let workspace!: ReturnType<typeof useStudentWorkspace>;
    function Probe() {
      workspace = useStudentWorkspace();
      snapshots.push({
        activeSessionId: workspace.activeSessionId,
        messages: workspace.messages.map((message) => message.content),
      });
      return null;
    }
    const view = render(<Probe />);
    await waitFor(() => expect(workspace.bootStatus).toBe("ready"));

    await act(async () => { workspace.selectSession("session-a"); });
    await waitFor(() => expect(workspace.messages.some((message) => message.content === "会话 A 内容")).toBe(true));
    snapshots.length = 0;

    await act(async () => {
      await workspace.deleteSession("session-a");
      await Promise.resolve();
    });

    expect(snapshots).not.toContainEqual(expect.objectContaining({
      activeSessionId: "session-b",
      messages: expect.arrayContaining(["会话 A 内容"]),
    }));
    view.unmount();
  });

  it("clears conversation state when authentication changes to another user", async () => {
    const userOne = {
      user_id: "user-1",
      csrf_token: "csrf-1",
      workspace_ids: ["default"],
      roles: ["student"],
      permissions: [],
      expires_at: 1_900_000_000,
    };
    const userTwo = { ...userOne, user_id: "user-2", csrf_token: "csrf-2" };
    ensureAuthMock.mockResolvedValue(userOne);
    vi.mocked(api.login).mockResolvedValue(userTwo);
    const wrapper = ({ children }: { children: React.ReactNode }) => <AuthProvider>{children}</AuthProvider>;
    const { result } = renderHook(() => useStudentWorkspace(), { wrapper });
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    await act(async () => { await result.current.send("用户一的私密内容"); });
    expect(result.current.messages.some((message) => message.content === "用户一的私密内容")).toBe(true);

    await act(async () => { await result.current.authenticate("user-2", "password"); });
    await waitFor(() => expect(result.current.authSession?.user_id).toBe("user-2"));

    expect(result.current.activeSessionId).toBeNull();
    expect(result.current.messages).toEqual([]);
  });

  it("ignores session list responses started under the previous authenticated user", async () => {
    const userOne = {
      user_id: "user-1",
      csrf_token: "csrf-1",
      workspace_ids: ["default"],
      roles: ["student"],
      permissions: [],
      expires_at: 1_900_000_000,
    };
    const userTwo = { ...userOne, user_id: "user-2", csrf_token: "csrf-2" };
    const sessionOne: SessionSummary = { session_id: "session-user-1", user_id: "user-1", workspace_id: "default", channel: "web" };
    const sessionTwo: SessionSummary = { session_id: "session-user-2", user_id: "user-2", workspace_id: "default", channel: "web" };
    ensureAuthMock.mockResolvedValue(userOne);
    vi.mocked(api.login).mockResolvedValue(userTwo);
    let listCallCount = 0;
    let resolveStale!: (response: { items: SessionSummary[] }) => void;
    vi.mocked(api.listSessions).mockImplementation(() => {
      listCallCount += 1;
      if (listCallCount === 1) return Promise.resolve({ items: [sessionOne] });
      if (listCallCount === 2) return new Promise((resolve) => { resolveStale = resolve; });
      return Promise.resolve({ items: [sessionTwo] });
    });
    const wrapper = ({ children }: { children: React.ReactNode }) => <AuthProvider>{children}</AuthProvider>;
    const { result } = renderHook(() => useStudentWorkspace(), { wrapper });
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    act(() => socketEventHandlerRef.current?.({
      v: "1",
      type: "session.updated",
      session_id: sessionOne.session_id,
      timestamp: "2026-09-10T00:00:00Z",
      payload: {},
    }));
    await waitFor(() => expect(listCallCount).toBe(2));

    await act(async () => { await result.current.authenticate("user-2", "password"); });
    await waitFor(() => expect(result.current.authSession?.user_id).toBe("user-2"));
    await waitFor(() => expect(result.current.sessions.map((session) => session.session_id)).toEqual([sessionTwo.session_id]));

    await act(async () => { resolveStale({ items: [sessionOne] }); });
    expect(result.current.sessions.map((session) => session.session_id)).toEqual([sessionTwo.session_id]);
  });

  it("creates the backend session in the resolved workspace only on the first message", async () => {
    ensureAuthMock.mockResolvedValue({ csrf_token: "x", workspace_ids: ["default", "research"] });
    getSettingsMock.mockResolvedValue({ preferences: { settings: { theme: "system", default_workspace_id: "research" } }, runtime });
    createSessionMock.mockResolvedValue({ session_id: "session-research", user_id: "user", workspace_id: "research", channel: "web" });
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    await act(async () => { await result.current.startNewChat(); });
    expect(createSessionMock).not.toHaveBeenCalled();

    await act(async () => { await result.current.send("解释 BERT"); });

    expect(createSessionMock).toHaveBeenCalledWith("research");
    expect(result.current.activeSessionId).toBe("session-research");
    expect(result.current.messages.some((message) => message.role === "user")).toBe(true);
  });

  it("sends the selected level independently of reasoning visibility", async () => {
    getSettingsMock.mockResolvedValue({
      preferences: { settings: { show_reasoning: false } },
      runtime,
    });
    createSessionMock.mockResolvedValue({ session_id: "session-thinking", user_id: "user", workspace_id: "default", channel: "web" });
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    await act(async () => { await result.current.send("解释 Transformer"); });

    expect(sendChatMock.mock.calls[0][6]).toBe("light");
  });

  it("starts a fresh backend session after a new chat while a previous creation is in flight", async () => {
    let resolveCreate: (session: { session_id: string; user_id: string; workspace_id: string; channel: string }) => void = () => undefined;
    createSessionMock.mockReturnValue(new Promise((resolve) => { resolveCreate = resolve; }));
    deleteSessionMock.mockClear();
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    let firstSend!: Promise<void>;
    await act(async () => { firstSend = result.current.send("第一个问题"); });
    expect(createSessionMock).toHaveBeenCalledTimes(1);

    await act(async () => { result.current.startNewChat(); });

    await act(async () => {
      resolveCreate({ session_id: "session-stale", user_id: "user", workspace_id: "default", channel: "web" });
      await firstSend;
    });

    expect(deleteSessionMock).toHaveBeenCalledWith("session-stale");
    expect(result.current.activeSessionId).toBeNull();
    expect(result.current.messages).toEqual([]);
    expect(sendChatMock).not.toHaveBeenCalled();

    createSessionMock.mockResolvedValue({ session_id: "session-fresh", user_id: "user", workspace_id: "default", channel: "web" });
    await act(async () => { await result.current.send("第二个问题"); });

    expect(createSessionMock).toHaveBeenCalledTimes(2);
    expect(result.current.activeSessionId).toBe("session-fresh");
    expect(sendChatMock).toHaveBeenCalledTimes(1);
    expect(sendChatMock.mock.calls[0][0]).toBe("session-fresh");
  });

  it("marks a running turn as cancelling immediately and uses the HTTP cancellation path", async () => {
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    await act(async () => { await result.current.send("请解释注意力机制"); });
    const requestId = sendChatMock.mock.calls[0][2] as string;
    act(() => socketEventHandlerRef.current?.({
      v: "1",
      type: "command.ack",
      request_id: requestId,
      session_id: "session-new",
      turn_id: "turn-1",
      timestamp: "2026-09-09T00:00:00Z",
      payload: { command: "chat.send" },
    }));
    act(() => socketEventHandlerRef.current?.({
      v: "1",
      type: "chat.started",
      session_id: "session-new",
      turn_id: "turn-1",
      timestamp: "2026-09-09T00:00:01Z",
      payload: {},
    }));
    expect(result.current.isRunning).toBe(true);

    act(() => result.current.cancel());

    expect(cancelTurnMock).toHaveBeenCalledWith("turn-1");
    expect(result.current.isCancelling).toBe(true);
    expect(socketCancelMock).not.toHaveBeenCalled();
  });

  it("settles the message from a successful HTTP cancellation response", async () => {
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    await act(async () => { await result.current.send("请停止这个回答"); });
    const requestId = sendChatMock.mock.calls[0][2] as string;
    act(() => socketEventHandlerRef.current?.({
      v: "1",
      type: "command.ack",
      request_id: requestId,
      session_id: "session-new",
      turn_id: "turn-http-cancel",
      timestamp: "2026-09-09T00:00:00Z",
      payload: { command: "chat.send" },
    }));
    act(() => socketEventHandlerRef.current?.({
      v: "1",
      type: "chat.started",
      session_id: "session-new",
      turn_id: "turn-http-cancel",
      timestamp: "2026-09-09T00:00:01Z",
      payload: {},
    }));

    await act(async () => {
      result.current.cancel();
      await Promise.resolve();
    });

    expect(result.current.isCancelling).toBe(false);
    expect(result.current.messages.find((message) => message.turnId === "turn-http-cancel" && message.role === "assistant")?.status).toBe("cancelled");
    expect(socketCancelMock).not.toHaveBeenCalled();
  });

  it("sends only one cancellation request for a rapid double click", async () => {
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    await act(async () => { await result.current.send("请停止这个回答"); });
    const requestId = sendChatMock.mock.calls[0][2] as string;
    act(() => socketEventHandlerRef.current?.({
      v: "1",
      type: "command.ack",
      request_id: requestId,
      session_id: "session-new",
      turn_id: "turn-double-click",
      timestamp: "2026-09-09T00:00:00Z",
      payload: { command: "chat.send" },
    }));
    act(() => socketEventHandlerRef.current?.({
      v: "1",
      type: "chat.started",
      session_id: "session-new",
      turn_id: "turn-double-click",
      timestamp: "2026-09-09T00:00:01Z",
      payload: {},
    }));

    act(() => {
      result.current.cancel();
      result.current.cancel();
    });

    expect(cancelTurnMock).toHaveBeenCalledTimes(1);
  });

  it("falls back to the WebSocket cancellation path when HTTP cancellation fails", async () => {
    cancelTurnMock.mockRejectedValueOnce(new Error("offline"));
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    await act(async () => { await result.current.send("请停止这个回答"); });
    const requestId = sendChatMock.mock.calls[0][2] as string;
    act(() => socketEventHandlerRef.current?.({
      v: "1",
      type: "command.ack",
      request_id: requestId,
      session_id: "session-new",
      turn_id: "turn-ws-fallback",
      timestamp: "2026-09-09T00:00:00Z",
      payload: { command: "chat.send" },
    }));
    act(() => socketEventHandlerRef.current?.({
      v: "1",
      type: "chat.started",
      session_id: "session-new",
      turn_id: "turn-ws-fallback",
      timestamp: "2026-09-09T00:00:01Z",
      payload: {},
    }));

    act(() => result.current.cancel());
    await waitFor(() => expect(socketCancelMock).toHaveBeenCalledWith("turn-ws-fallback"));
    expect(result.current.isCancelling).toBe(true);
  });

  it("releases the composer when cancellation has no transport acknowledgement", async () => {
    cancelTurnMock.mockRejectedValueOnce(new Error("HTTP 500"));
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    await act(async () => { await result.current.send("搜索测试"); });
    const requestId = sendChatMock.mock.calls[0][2] as string;
    act(() => socketEventHandlerRef.current?.({
      v: "1",
      type: "command.ack",
      request_id: requestId,
      session_id: "session-new",
      turn_id: "turn-cancel-unconfirmed",
      timestamp: "2026-09-10T00:00:00Z",
      payload: { command: "chat.send" },
    }));
    act(() => socketEventHandlerRef.current?.({
      v: "1",
      type: "chat.started",
      session_id: "session-new",
      turn_id: "turn-cancel-unconfirmed",
      timestamp: "2026-09-10T00:00:01Z",
      payload: {},
    }));

    act(() => result.current.cancel());
    await waitFor(() => expect(socketCancelMock).toHaveBeenCalledWith("turn-cancel-unconfirmed"));

    await waitFor(() => expect(result.current.isRunning).toBe(false), { timeout: 4_000 });
  });

  it("preserves the student WebSocket while expired authentication is restored", async () => {
  ensureAuthMock.mockResolvedValue({
    user_id: "user-1",
    csrf_token: "csrf-1",
    workspace_ids: ["default"],
    roles: ["guest"],
    permissions: [],
    expires_at: 1_900_000_000,
  });

  vi.mocked(api.login).mockResolvedValue({
    user_id: "user-1",
    csrf_token: "csrf-2",
    workspace_ids: ["default"],
    roles: ["guest"],
    permissions: [],
    expires_at: 1_900_000_100,
  });

  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <AuthProvider>{children}</AuthProvider>
  );

  const { result } = renderHook(() => useStudentWorkspace(), { wrapper });

  await waitFor(() => {
    expect(result.current.bootStatus).toBe("ready");
  });

  expect(socketConnectMock).toHaveBeenCalledTimes(1);
  expect(socketCloseMock).not.toHaveBeenCalled();

  act(() => {
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
  });

  await act(async () => {
    await result.current.authenticate("user", "password");
  });

  await waitFor(() => {
    expect(result.current.bootStatus).toBe("ready");
  });

  expect(socketConnectMock).toHaveBeenCalledTimes(1);
  expect(socketCloseMock).not.toHaveBeenCalled();
});

it("clears the previous request error after expired authentication is restored", async () => {
  ensureAuthMock.mockResolvedValue({
    user_id: "user-1",
    csrf_token: "csrf-1",
    workspace_ids: ["default"],
    roles: ["guest"],
    permissions: [],
    expires_at: 1_900_000_000,
  });

  vi.mocked(api.login).mockResolvedValue({
    user_id: "user-1",
    csrf_token: "csrf-2",
    workspace_ids: ["default"],
    roles: ["guest"],
    permissions: [],
    expires_at: 1_900_000_100,
  });

  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <AuthProvider>{children}</AuthProvider>
  );

  const { result } = renderHook(() => useStudentWorkspace(), { wrapper });

  await waitFor(() => {
    expect(result.current.bootStatus).toBe("ready");
  });

  act(() => {
socketEventHandlerRef.current?.({
  v: "1",
  type: "command.error",
  timestamp: "2026-08-29T00:00:00Z",
  payload: {
    message: "Authentication required",
  },
});
});

  expect(result.current.requestError).toEqual({ code: "request_failed", message: "Authentication required" });

  act(() => {
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
  });

  await act(async () => {
    await result.current.authenticate("user", "password");
  });

  await waitFor(() => {
    expect(result.current.bootStatus).toBe("ready");
  });

  expect(result.current.requestError).toBeNull();
});
it("uses the global auth session and logout boundary when mounted in the application", async () => {
    ensureAuthMock.mockResolvedValue({
      user_id: "user-1",
      csrf_token: "csrf-1",
      workspace_ids: ["default"],
      roles: ["guest"],
      expires_at: 1_900_000_000,
    });
    const wrapper = ({ children }: { children: React.ReactNode }) => <AuthProvider>{children}</AuthProvider>;
    const { result } = renderHook(() => useStudentWorkspace(), { wrapper });

    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));
    await act(async () => { await result.current.logout(); });

    expect(api.logout).toHaveBeenCalledTimes(1);
    expect(result.current.bootStatus).toBe("unauthenticated");
    expect(result.current.authSession).toBeNull();
  });
});
