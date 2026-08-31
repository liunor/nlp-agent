import { act, renderHook, waitFor } from "@testing-library/react";

import { useStudentWorkspace } from "./useStudentWorkspace";
import { api } from "@/platform/http/api";
import { AuthProvider } from "@/platform/auth/AuthContext";

const runtime = {
  default_model_profile: "deepseek",
  model_profiles: {
    deepseek: { label: "DeepSeek", provider: "deepseek", available: true },
    qwen: { label: "Qwen", provider: "dashscope", available: true },
  },
};

const { ensureAuthMock, getSettingsMock, createSessionMock, deleteSessionMock, sendChatMock, renameSessionMock } = vi.hoisted(() => ({
  ensureAuthMock: vi.fn(),
  getSettingsMock: vi.fn(),
  createSessionMock: vi.fn(),
  deleteSessionMock: vi.fn(async () => undefined),
  sendChatMock: vi.fn(),
  renameSessionMock: vi.fn(),
}));

vi.mock("@/platform/http/api", () => ({
  ensureAuth: ensureAuthMock,
  api: {
    listSessions: vi.fn(async () => ({ items: [] })),
    getSettings: getSettingsMock,
    createSession: createSessionMock,
    deleteSession: deleteSessionMock,
    renameSession: renameSessionMock,
    login: vi.fn(),
    logout: vi.fn(async () => undefined),
    updateSettings: vi.fn(),
  },
}));

vi.mock("@/platform/realtime/client", () => ({
  StudentSocket: class {
    connect() {}
    close() {}
    setSession() {}
    sendChat(...args: unknown[]) { sendChatMock(...args); }
  },
}));

describe("useStudentWorkspace settings", () => {
  let dark = false;
  let onChange: (() => void) | undefined;

  beforeEach(() => {
    localStorage.clear();
    dark = false;
    onChange = undefined;
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
    createSessionMock.mockClear();
    deleteSessionMock.mockClear();
    sendChatMock.mockClear();
    renameSessionMock.mockClear();
  });

  it("rolls back optimistic settings and exposes a visible error on network failure", async () => {
    vi.mocked(api.updateSettings).mockRejectedValueOnce(new Error("offline"));
    const { result } = renderHook(() => useStudentWorkspace());
    await waitFor(() => expect(result.current.bootStatus).toBe("ready"));

    await act(async () => { await result.current.patchSettings({ theme: "dark" }); });

    expect(result.current.settings.theme).toBe("system");
    expect(result.current.settingsError).toContain("offline");
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
    expect(result.current.requestError).toContain("重命名失败");
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
