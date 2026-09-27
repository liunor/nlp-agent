import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/platform/http/api";

import { FilesPanel } from "./FilesPanel";

vi.mock("@/platform/http/api", () => ({
  api: {
    getStorageUsage: vi.fn(),
    listStorageFiles: vi.fn(),
    createStorageFolder: vi.fn(),
    uploadStorageFile: vi.fn(),
    renameStorageFile: vi.fn(),
    deleteStorageFile: vi.fn(),
  },
  storageFileDownloadUrl: (fileId: string) => `/api/v1/storage/files/${fileId}/download`,
}));

const usage = {
  role: "student",
  core: { used_bytes: 90, quota_bytes: 100, used_ratio: 0.9, state: "critical" as const },
  files: { used_bytes: 95, quota_bytes: 100, used_ratio: 0.95, state: "critical" as const },
  files_count: 1,
  max_file_bytes: 10,
  max_items: 500,
};

describe("FilesPanel", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.mocked(api.getStorageUsage).mockResolvedValue(usage);
    vi.mocked(api.listStorageFiles).mockResolvedValue({ items: [] });
  });

  it("shows independent Windows-like meters for core and personal file space", async () => {
    render(<FilesPanel workspaceId="workspace-1" />);

    expect(await screen.findByRole("progressbar", { name: "通用空间" })).toHaveAttribute("aria-valuenow", "90");
    expect(screen.getByRole("progressbar", { name: "个人文件" })).toHaveAttribute("aria-valuenow", "95");
    expect(screen.getByRole("progressbar", { name: "通用空间" }).parentElement?.parentElement).toHaveClass("critical");
    expect(api.getStorageUsage).toHaveBeenCalledWith("workspace-1");
  });

  it("keeps the learning-document importer behind a separate tab", async () => {
    render(<FilesPanel workspaceId="workspace-1" />);

    fireEvent.click(screen.getByRole("tab", { name: "学习文档导入" }));
    expect(screen.getByText("导入学习文档")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "我的文件" }));
    await waitFor(() => expect(screen.getByText("此文件夹为空")).toBeInTheDocument());
  });
});
