import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { FileDistributionPage } from "./FileDistributionPage";
import { api } from "@/platform/http/api";

vi.mock("@/platform/http/api", () => ({
  api: {
    listStorageFiles: vi.fn(),
    getDistributionTargets: vi.fn(),
    distributeFile: vi.fn(),
    listDistributionHistory: vi.fn(),
    uploadStorageFile: vi.fn(),
    lookupTransferRecipient: vi.fn(),
  },
}));

describe("FileDistributionPage", () => {
  const mockFiles = [
    {
      id: "file-1",
      name: "NLP实验指导书.pdf",
      kind: "file" as const,
      size_bytes: 2048,
      mime_type: "application/pdf",
      created_at: "2026-11-18T10:00:00Z",
      updated_at: "2026-11-18T10:00:00Z",
    },
    {
      id: "file-2",
      name: "张量与形状.md",
      kind: "file" as const,
      size_bytes: 1024,
      mime_type: "text/markdown",
      created_at: "2026-11-18T10:00:00Z",
      updated_at: "2026-11-18T10:00:00Z",
    },
  ];

  const mockTargets = {
    summary: {
      student_count: 45,
      total_user_count: 50,
    },
    roles: [
      { code: "student", name: "所有学生", description: "系统内所有已注册学生用户" },
      { code: "guest", name: "所有游客", description: "系统内所有试用/游客用户" },
      { code: "teacher", name: "所有教师", description: "系统内所有教学人员" },
    ],
    classrooms: [
      { id: "class-1", name: "自然语言处理一班", workspace_id: "default", student_count: 30 },
      { id: "class-2", name: "深度学习进阶班", workspace_id: "default", student_count: 15 },
    ],
  };

  const mockHistory = [
    {
      id: "dist-1",
      file_name: "第1周课件.pdf",
      size_bytes: 5120,
      target_type: "all_students",
      target_label: "所有学生",
      direct_delivery: true,
      total_count: 45,
      success_count: 45,
      failed_count: 0,
      status: "completed",
      details: [],
      created_at: "2026-11-18T09:00:00Z",
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listStorageFiles).mockResolvedValue({ items: mockFiles });
    vi.mocked(api.getDistributionTargets).mockResolvedValue(mockTargets);
    vi.mocked(api.listDistributionHistory).mockResolvedValue({ items: mockHistory });
  });

  it("renders the distribution page with targets and files", async () => {
    render(<FileDistributionPage workspaceId="default" />);

    expect(await screen.findByRole("heading", { name: "教师文件分发" })).toBeVisible();
    expect(await screen.findByText("NLP实验指导书.pdf (2.0 KB)")).toBeVisible();
    expect(screen.getByText(/系统内所有注册学生 \(45 人\)/)).toBeVisible();
    expect(screen.getByText(/平台全部活跃用户 \(50 人\)/)).toBeVisible();
    expect(screen.getByText("第1周课件.pdf")).toBeVisible();
  });

  it("distributes file to all students with direct delivery", async () => {
    vi.mocked(api.distributeFile).mockResolvedValue({
      distribution_id: "dist-new",
      source_file_id: "file-1",
      file_name: "NLP实验指导书.pdf",
      size_bytes: 2048,
      target_type: "all_students",
      target_value: null,
      target_label: "所有学生",
      direct_delivery: true,
      total_targets: 45,
      success_count: 45,
      failed_count: 0,
      failures: [],
      created_at: "2026-11-18T10:30:00Z",
    });

    render(<FileDistributionPage workspaceId="default" />);

    const sendBtn = await screen.findByRole("button", { name: "立即发送文件" });
    fireEvent.click(sendBtn);

    await waitFor(() => {
      expect(api.distributeFile).toHaveBeenCalledWith({
        source_file_id: "file-1",
        target_type: "all_students",
        target_value: null,
        direct_delivery: true,
        workspace_id: "default",
      });
    });

    expect(await screen.findByText(/分发完成！已向 所有学生 发送文件/)).toBeVisible();
    expect(screen.getAllByText("45 / 45 人").length).toBeGreaterThanOrEqual(1);
  });

  it("supports switching to classroom and role distribution", async () => {
    render(<FileDistributionPage workspaceId="default" />);

    await screen.findByRole("heading", { name: "教师文件分发" });

    // Switch to classroom
    fireEvent.click(screen.getByText("指定某个班级"));
    expect(await screen.findByText("自然语言处理一班 (30 名成员)")).toBeVisible();

    // Switch to role
    fireEvent.click(screen.getByText("按角色分类发送"));
    expect(await screen.findByText("所有游客 - 系统内所有试用/游客用户")).toBeVisible();
  });
});
