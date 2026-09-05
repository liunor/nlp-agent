import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { Agents, Models, Tools } from "./DeveloperWorkspace";

const {
  deleteWorkerProfileMock,
  saveModelPresetMock,
  saveModelProviderMock,
  saveModelRouteMock,
  saveWorkerProfileMock,
  updateToolPoliciesMock,
} = vi.hoisted(() => ({
  deleteWorkerProfileMock: vi.fn(),
  saveModelPresetMock: vi.fn(),
  saveModelProviderMock: vi.fn(),
  saveModelRouteMock: vi.fn(),
  saveWorkerProfileMock: vi.fn(),
  updateToolPoliciesMock: vi.fn(),
}));

vi.mock("@/platform/http/api", () => ({
  api: {
    deleteWorkerProfile: deleteWorkerProfileMock,
    saveModelPreset: saveModelPresetMock,
    saveModelProvider: saveModelProviderMock,
    saveModelRoute: saveModelRouteMock,
    saveWorkerProfile: saveWorkerProfileMock,
    updateToolPolicies: updateToolPoliciesMock,
  },
}));

const snapshot = {
  agents: {
    runtime: {
      coordinator: { max_iterations: 12, max_tool_calls: 32 },
      worker: { max_injections: 15 },
    },
    profiles: {
      researcher: {
        description: "检索资料并整理证据",
        model: "worker-fast",
        execution_mode: "react",
        requires_native_search: false,
        inherit_tool_policy: true,
        skills: ["research"],
        capabilities: ["web.fetch"],
        allowed_tools: ["web_fetch"],
        denied_tools: [],
      },
    },
    overrides: {},
  },
  tools: {
    catalog_revision: 3,
    items: [
      {
        name: "nlp_tfidf_analyzer",
        source: "custom",
        provider: "nlp-teaching-tools",
        category: "nlp",
        description: "计算 TF-IDF",
        scopes: ["worker"],
        capabilities: ["nlp.analyze"],
        risk: "low",
        timeout_s: 30,
      },
      {
        name: "sandbox_status",
        source: "builtin",
        provider: "sandbox",
        category: "sandbox",
        description: "查看沙箱状态",
        scopes: ["coordinator", "worker"],
        capabilities: ["sandbox.observe"],
        risk: "low",
        timeout_s: 15,
      },
      {
        name: "web_fetch",
        source: "builtin",
        provider: "web-access",
        category: "general",
        description: "读取公开 URL",
        scopes: ["worker"],
        capabilities: ["web.fetch"],
        risk: "medium",
        timeout_s: 35,
      },
    ],
    policies: {
      coordinator: { allowed_tools: [], allowed_capabilities: [], denied_tools: [], denied_capabilities: [] },
      worker: { allowed_tools: [], allowed_capabilities: [], denied_tools: [], denied_capabilities: [] },
    },
    mcp_servers: {},
    custom: {},
  },
  models: {
    providers: {
      deepseek: {
        adapter: "deepseek",
        base_url: "https://api.deepseek.com",
        api_key_env: "DEEPSEEK_API_KEY",
        api_key_configured: true,
      },
    },
    models: {
      "deepseek-v4-flash": {
        provider: "deepseek",
        model_id: "deepseek-v4-flash",
        context_window_tokens: 1000000,
        max_output_tokens: 384000,
        capabilities: { streaming: true, tool_calls: true, thinking: true },
      },
    },
    presets: {
      "worker-fast": {
        model: "deepseek-v4-flash",
        thinking: { enabled: true, effort: "high" },
        generation: { max_output_tokens: 24000, temperature: 0.2 },
        native_search: { enabled: false, forced: false, strategy: "turbo" },
        timeouts: { connect_s: 10, first_token_s: 120, stream_idle_s: 60, total_s: 360 },
        retry: { max_attempts: 2, base_delay_s: 1, max_delay_s: 8, jitter: "full" },
        circuit_breaker: { failure_threshold: 5, cooldown_s: 60 },
      },
    },
    routes: {
      worker: { primary: "worker-fast", fallbacks: [] },
    },
    defaults: { model_profile: "deepseek" },
  },
} as const;

describe("developer control-plane configuration", () => {
  beforeEach(() => {
    deleteWorkerProfileMock.mockReset();
    saveModelPresetMock.mockReset();
    saveModelProviderMock.mockReset();
    saveModelRouteMock.mockReset();
    saveWorkerProfileMock.mockReset();
    updateToolPoliciesMock.mockReset();
    window.history.replaceState({}, "", "/developer/tools");
  });

  it("edits a Worker Profile through purpose-built fields", async () => {
    render(<Agents snapshot={snapshot as never} refresh={vi.fn(async () => undefined)} />);

    expect(screen.getByRole("heading", { name: "Agent 与 Worker" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /researcher/ }));
    fireEvent.change(screen.getByLabelText("用途说明"), { target: { value: "专门整理检索证据" } });
    fireEvent.click(screen.getByRole("button", { name: "保存 Profile" }));

    await waitFor(() => expect(saveWorkerProfileMock).toHaveBeenCalledWith(
      "researcher",
      expect.objectContaining({ description: "专门整理检索证据", skills: ["research"] }),
    ));
  });

  it("routes tools through NLP, Sandbox, and other categories and saves role grants", async () => {
    render(<Tools snapshot={snapshot as never} refresh={vi.fn(async () => undefined)} />);

    fireEvent.click(screen.getByRole("tab", { name: /NLP 专属/ }));
    expect(screen.getAllByText("nlp_tfidf_analyzer").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("sandbox_status")).not.toBeInTheDocument();
    expect(window.location.search).toContain("group=nlp");

    fireEvent.click(screen.getByRole("checkbox", { name: "授权 nlp_tfidf_analyzer" }));
    fireEvent.click(screen.getByRole("button", { name: "保存 Worker 权限" }));

    await waitFor(() => expect(updateToolPoliciesMock).toHaveBeenCalledWith(
      expect.objectContaining({
        worker: expect.objectContaining({ allowed_tools: ["nlp_tfidf_analyzer"] }),
      }),
    ));
  });

  it("edits a Provider and validates model preset changes from the model page", async () => {
    const refresh = vi.fn(async () => undefined);
    render(<Models snapshot={snapshot as never} refresh={refresh} />);

    fireEvent.change(screen.getByLabelText("deepseek 服务地址"), { target: { value: "https://proxy.example/v1" } });
    fireEvent.click(screen.getByRole("button", { name: "保存 Provider" }));
    await waitFor(() => expect(saveModelProviderMock).toHaveBeenCalledWith(
      "deepseek",
      expect.objectContaining({ base_url: "https://proxy.example/v1" }),
    ));
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("tab", { name: "模型预设" }));
    fireEvent.change(screen.getByLabelText("worker-fast 最大输出 Token"), { target: { value: "16000" } });
    fireEvent.click(screen.getByRole("button", { name: "保存模型预设" }));
    await waitFor(() => expect(saveModelPresetMock).toHaveBeenCalledWith(
      "worker-fast",
      expect.objectContaining({ generation: expect.objectContaining({ max_output_tokens: 16000 }) }),
    ));
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(2));
  });

  it("saves the selected model route with a primary and fallback chain", async () => {
    render(<Models snapshot={snapshot as never} refresh={vi.fn(async () => undefined)} />);

    fireEvent.click(screen.getByRole("tab", { name: "路由与故障转移" }));
    fireEvent.change(screen.getByLabelText("worker 主路由预设"), { target: { value: "worker-fast" } });
    fireEvent.click(screen.getByRole("button", { name: "保存模型路由" }));

    await waitFor(() => expect(saveModelRouteMock).toHaveBeenCalledWith(
      "worker",
      expect.objectContaining({ primary: "worker-fast", fallbacks: [] }),
    ));
  });
});
