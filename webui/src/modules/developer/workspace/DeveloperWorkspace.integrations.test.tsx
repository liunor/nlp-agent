import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { Automations, Mcp, Skills } from "./DeveloperWorkspace";

const { deleteMcpMock, getSkillMock, saveMcpMock, saveSkillMock, testMcpMock } = vi.hoisted(() => ({
  deleteMcpMock: vi.fn(),
  getSkillMock: vi.fn(),
  saveMcpMock: vi.fn(),
  saveSkillMock: vi.fn(),
  testMcpMock: vi.fn(),
}));
vi.mock("@/platform/http/api", () => ({
  api: {
    deleteMcp: deleteMcpMock,
    deleteSkill: vi.fn(),
    getSkill: getSkillMock,
    saveMcp: saveMcpMock,
    saveSkill: saveSkillMock,
    testMcp: testMcpMock,
  },
}));

const snapshot = {
  tools: {
    mcp_servers: {
      local: { transport: "stdio", command: "python", args: ["server.py"], credentials_configured: true },
    },
    items: [
      { name: "mcp_local_search", source: "mcp", provider: "local", description: "Search", scopes: ["worker"] },
    ],
  },
  skills: [
    { name: "research", path: ".data/skills/research/SKILL.md", source: "workspace", description: "检索资料", allowed_tools: ["mcp_local_search"], capabilities: [], available: false, missing_requirements: ["bin:rg"], bytes: 120, modified_at: 0 },
    { name: "teacher", path: "skills/teacher/SKILL.md", source: "project", description: "教学流程", allowed_tools: [], capabilities: [], available: true, missing_requirements: [], bytes: 80, modified_at: 0 },
  ],
  agents: { profiles: { researcher: { skills: ["research"] } } },
  features: {
    apps: { available: false, reason: "No app registry is configured" },
    automations: { available: false, reason: "Cron runtime is not enabled" },
  },
};

describe("developer integrations", () => {
  beforeEach(() => {
    deleteMcpMock.mockReset();
    getSkillMock.mockReset();
    saveMcpMock.mockReset();
    saveSkillMock.mockReset();
    testMcpMock.mockReset();
    testMcpMock.mockResolvedValue({ ok: true, server: "local", tools: ["mcp_local_search"] });
    getSkillMock.mockResolvedValue({ name: "research", content: "---\nname: research\ndescription: 检索资料\n---\n\n# Research SOP\n\nUse evidence." });
  });

  it("makes configured MCP servers editable and exposes their discovered tools", async () => {
    render(<Mcp snapshot={snapshot as never} refresh={vi.fn(async () => undefined)} />);

    expect(screen.getByText("已连接")).toBeVisible();
    expect(screen.getByText("mcp_local_search")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "编辑 local" }));
    expect(screen.getByLabelText("MCP 名称")).toHaveValue("local");
    expect(screen.getByLabelText("stdio 命令")).toHaveValue("python");

    fireEvent.click(screen.getByRole("button", { name: "测试连接" }));
    await waitFor(() => expect(screen.getByText(/连接成功/)).toBeVisible());
    expect(testMcpMock).toHaveBeenCalledWith("local", expect.objectContaining({ command: "python", transport: "stdio" }));
  });

  it("explains Skill availability, usage, and previews the actual SOP", async () => {
    render(<Skills snapshot={snapshot as never} refresh={vi.fn(async () => undefined)} />);

    expect(screen.getByText("缺少依赖：bin:rg")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /research/ }));
    expect(screen.getByText("researcher")).toBeVisible();
    await waitFor(() => expect(screen.getByLabelText("Skill 名称")).toHaveValue("research"));
    fireEvent.click(screen.getByRole("button", { name: "预览" }));
    expect(await screen.findByRole("heading", { name: "Research SOP" })).toBeVisible();
  });

  it("makes the unimplemented Apps surface honest instead of presenting a dead control", () => {
    render(<Automations snapshot={snapshot as never} />);

    expect(screen.getByRole("heading", { name: "Apps 与自动化" })).toBeVisible();
    expect(screen.getAllByText("规划中").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("Apps Registry 未启用")).not.toBeInTheDocument();
  });
});
