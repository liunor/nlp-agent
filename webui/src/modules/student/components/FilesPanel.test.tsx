import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/platform/http/api";
import type { UserProfile } from "@/shared/types";

import { FilesPanel, parseUtcTimestamp } from "./FilesPanel";

vi.mock("@/platform/http/api", () => ({
  api: {
    getStorageUsage: vi.fn(),
    listStorageFiles: vi.fn(),
    createStorageFolder: vi.fn(),
    uploadStorageFile: vi.fn(),
    renameStorageFile: vi.fn(),
    readStorageFile: vi.fn(),
    deleteStorageFile: vi.fn(),
    listStorageTrash: vi.fn(),
    restoreStorageFile: vi.fn(),
    permanentlyDeleteStorageFile: vi.fn(),
    lookupTransferRecipient: vi.fn(),
    preflightFileTransfer: vi.fn(),
    createFileTransfer: vi.fn(),
    listFileTransfers: vi.fn(),
    getFileTransferSummary: vi.fn(),
    getFileTransferUnreadSummary: vi.fn(),
    markFileTransfersSeen: vi.fn(),
    acceptFileTransfer: vi.fn(),
    rejectFileTransfer: vi.fn(),
    cancelFileTransfer: vi.fn(),
    getCurrentUser: vi.fn(),
    getDistributionTargets: vi.fn(),
    distributeFile: vi.fn(),
  },
  storageFileDownloadUrl: (fileId: string, workspaceId?: string) => `/api/v1/storage/files/${fileId}/download${workspaceId ? `?workspace_id=${workspaceId}` : ""}`,
}));

vi.mock("./DocumentCodeView", () => ({
  DocumentCodeView: ({ code, language }: { code: string; language: string }) => <pre data-testid="code-preview" data-language={language}>{code}</pre>,
}));
vi.mock("./MarkdownContent", () => ({
  MarkdownContent: ({ children }: { children: string }) => <div data-testid="markdown-preview">{children}</div>,
}));

const usage = {
  role: "student",
  core: { used_bytes: 90, quota_bytes: 100, used_ratio: 0.9, state: "critical" as const },
  files: { used_bytes: 95, quota_bytes: 100, used_ratio: 0.95, state: "critical" as const },
  files_count: 1,
  max_file_bytes: 10,
  max_items: 500,
};

const mockUser = (roles: string[] = ["student"]): UserProfile => ({
  id: "user-1",
  identity_id: "NV1234567890123",
  username: "test_user",
  display_name: "Test User",
  status: "active",
  created_at: "2026-03-24T00:00:00Z",
  updated_at: "2026-03-24T00:00:00Z",
  roles,
});

describe("FilesPanel", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.mocked(api.getStorageUsage).mockResolvedValue(usage);
    vi.mocked(api.listStorageFiles).mockResolvedValue({ items: [] });
    vi.mocked(api.listStorageTrash).mockResolvedValue({ items: [] });
    vi.mocked(api.getFileTransferSummary).mockResolvedValue({ pending_count: 0, notification_version: 0 });
    vi.mocked(api.getFileTransferUnreadSummary).mockResolvedValue({ pending_count: 0, unseen_accepted_count: 0, unread_count: 0, notification_version: 0 });
    vi.mocked(api.markFileTransfersSeen).mockResolvedValue({ ok: true, updated: 0 });
    vi.mocked(api.listFileTransfers).mockResolvedValue({ items: [] });
    vi.mocked(api.getCurrentUser).mockResolvedValue(mockUser(["student"]));
    vi.mocked(api.getDistributionTargets).mockResolvedValue({
      summary: { student_count: 42, guest_count: 5, admin_count: 2, total_user_count: 50 },
      roles: [{ code: "student", name: "全体学生", description: "" }],
      classrooms: [{ id: "class-1", name: "NLP自然语言处理1班", workspace_id: "ws-1", student_count: 30 }],
    });
  });

  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it("shows independent Windows-like meters for core and personal file space", async () => {
    render(<FilesPanel workspaceId="workspace-1" />);

    expect(await screen.findByRole("progressbar", { name: "通用空间" })).toHaveAttribute("aria-valuenow", "90");
    expect(screen.getByRole("progressbar", { name: "个人文件" })).toHaveAttribute("aria-valuenow", "95");
    expect(screen.getByRole("progressbar", { name: "通用空间" }).parentElement).toHaveClass("critical");
    expect(api.getStorageUsage).toHaveBeenCalledWith("workspace-1");
  });

  it("refreshes files and usage after sandbox execution changes platform files", async () => {
    render(<FilesPanel workspaceId="workspace-1" />);
    await screen.findByText("此文件夹为空");
    vi.mocked(api.listStorageFiles).mockClear();
    vi.mocked(api.getStorageUsage).mockClear();
    fireEvent(window, new Event("sandbox:files-changed"));
    await waitFor(() => expect(api.listStorageFiles).toHaveBeenCalledWith("workspace-1", undefined));
    expect(api.getStorageUsage).toHaveBeenCalledWith("workspace-1");
  });

  it("keeps the learning-document importer behind a separate tab", async () => {
    render(<FilesPanel workspaceId="workspace-1" />);

    fireEvent.click(screen.getByRole("tab", { name: "学习文档导入" }));
    expect(screen.getByText("导入学习文档")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "我的文件" }));
    await waitFor(() => expect(screen.getByText("此文件夹为空")).toBeInTheDocument());
  });

  it("renders a compact attach icon and forwards the selected file", async () => {
    const file = { id: "attach", kind: "file" as const, name: "报告.docx", size_bytes: 10, mime_type: null, created_at: null, updated_at: null };
    vi.mocked(api.listStorageFiles).mockResolvedValue({ items: [file] });
    const attached = vi.fn();
    window.addEventListener("file-space:attach", attached);
    try {
      render(<FilesPanel workspaceId="w" />);
      const button = await screen.findByRole("button", { name: "添加到对话 报告.docx" });
      expect(button.textContent).toBe("");
      expect(button.querySelector("svg")).toBeInTheDocument();
      expect(button).toHaveAttribute("title", "添加到当前对话");
      fireEvent.click(button);
      expect(attached).toHaveBeenCalledTimes(1);
      expect((attached.mock.calls[0][0] as CustomEvent).detail).toEqual({ file, workspaceId: "w" });
    } finally { window.removeEventListener("file-space:attach", attached); }
  });

  it("keeps multiple file previews open, switches without refetching, and closes without deleting", async () => {
    const documents = ["first.md", "second.md"].map((name, index) => ({ id: `multi-${index}`, kind: "file" as const, name, size_bytes: 10, mime_type: "text/markdown", created_at: null, updated_at: null }));
    vi.mocked(api.listStorageFiles).mockResolvedValue({ items: documents });
    vi.mocked(api.readStorageFile).mockImplementation(async id => ({ content: id === "multi-0" ? "first content" : "second content", truncated: false, mime_type: "text/markdown", bytes_read: 10 }));
    render(<FilesPanel workspaceId="w" />);
    fireEvent.click(await screen.findByRole("button", { name: "查看 first.md" }));
    await screen.findByText("first content");
    fireEvent.click(screen.getByRole("tab", { name: "我的文件" }));
    fireEvent.click(await screen.findByRole("button", { name: "查看 second.md" }));
    await screen.findByText("second content");
    const tabs = screen.getByRole("tablist", { name: "已打开的文件" });
    expect(within(tabs).getAllByRole("tab")).toHaveLength(2);
    fireEvent.click(within(tabs).getByRole("tab", { name: "first.md" }));
    expect(screen.getByRole("tabpanel", { name: "first.md" })).toHaveTextContent("first content");
    expect(api.readStorageFile).toHaveBeenCalledTimes(2);
    fireEvent.click(within(tabs).getByRole("button", { name: "关闭 first.md" }));
    expect(within(tabs).queryByRole("tab", { name: "first.md" })).not.toBeInTheDocument();
    expect(screen.getByRole("tabpanel", { name: "second.md" })).toHaveTextContent("second content");
    expect(api.deleteStorageFile).not.toHaveBeenCalled();
  });

  it("deduplicates reopened files and clears tabs when the account changes", async () => {
    const request = { id: "scope-file", name: "scope.md", url: "", mediaType: "text/markdown", bytes: 5, source: "storage" as const, workspaceId: "w" };
    vi.mocked(api.readStorageFile).mockResolvedValue({ content: "private document", truncated: false, mime_type: "text/markdown", bytes_read: 5 });
    const { rerender } = render(<FilesPanel userId="alice" workspaceId="w" previewRequest={request} />);
    fireEvent.click(screen.getByRole("tab", { name: "学习文档导入" }));
    await screen.findByText("private document");
    rerender(<FilesPanel userId="alice" workspaceId="w" previewRequest={{ ...request }} />);
    const tabs = screen.getByRole("tablist", { name: "已打开的文件" });
    expect(within(tabs).getAllByRole("tab")).toHaveLength(1);
    rerender(<FilesPanel userId="bob" workspaceId="w" />);
    fireEvent.click(screen.getByRole("tab", { name: "学习文档导入" }));
    expect(screen.queryByRole("tablist", { name: "已打开的文件" })).not.toBeInTheDocument();
    expect(screen.queryByText("private document")).not.toBeInTheDocument();
  });

  it("opens a batch of local documents in separate tabs and closes without removing the import", async () => {
    render(<FilesPanel userId="alice" workspaceId="w" />);
    upload([markdownFile("a.md", "first local"), markdownFile("b.md", "second local")]);
    const tabs = await screen.findByRole("tablist", { name: "已打开的文件" });
    await waitFor(() => expect(within(tabs).getAllByRole("tab")).toHaveLength(2));
    fireEvent.click(within(tabs).getByRole("tab", { name: "a.md" }));
    expect(screen.getByRole("tabpanel", { name: "a.md" })).toHaveTextContent("first local");
    fireEvent.click(within(tabs).getByRole("button", { name: "关闭 a.md" }));
    expect(screen.getByRole("button", { name: "预览 a.md" })).toBeInTheDocument();
    expect(localStorage.getItem("nlp-agent.imported-files.v1:alice:w")).toContain("first local");
  });

  it("automatically activates a newly requested textbook preview", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, text: async () => "new textbook" }));
    const { rerender } = render(<FilesPanel userId="alice" workspaceId="w" />);
    const request = { id: "new-book", name: "book.md", url: "/book/file", mediaType: "text/markdown", bytes: 12 };
    rerender(<FilesPanel userId="alice" workspaceId="w" previewRequest={request} />);
    expect(screen.getByRole("tab", { name: "学习文档导入" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("tabpanel", { name: "book.md" })).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "我的文件" }));
    rerender(<FilesPanel userId="alice" workspaceId="w" previewRequest={request} />);
    expect(screen.getByRole("tab", { name: "我的文件" })).toHaveAttribute("aria-selected", "true");
  });

  it("does not restore an asynchronous import after clearing files", async () => {
    let finish!: (content: string) => void;
    const pending = new Promise<string>(resolve => { finish = resolve; });
    const slow = markdownFile("slow.md");
    const prefix = new Blob();
    vi.spyOn(prefix, "text").mockReturnValue(pending);
    vi.spyOn(slow, "slice").mockReturnValue(prefix);
    render(<FilesPanel userId="alice" workspaceId="w" />);
    upload([markdownFile("initial.md")]);
    await screen.findByRole("button", { name: "预览 initial.md" });
    upload([slow]);
    fireEvent.click(screen.getByRole("button", { name: "清空" }));
    await act(async () => { finish("late private data"); await pending; });
    expect(screen.queryByRole("tab", { name: "slow.md" })).not.toBeInTheDocument();
    expect(localStorage.getItem("nlp-agent.imported-files.v1:alice:w")).toBe("[]");
  });

  it("does not persist a pending import after switching accounts", async () => {
    let finish!: (content: string) => void;
    const pending = new Promise<string>(resolve => { finish = resolve; });
    const slow = markdownFile("slow.md"), prefix = new Blob();
    vi.spyOn(prefix, "text").mockReturnValue(pending);
    vi.spyOn(slow, "slice").mockReturnValue(prefix);
    const { rerender } = render(<FilesPanel userId="alice" workspaceId="w" />);
    upload([slow]);
    rerender(<FilesPanel userId="bob" workspaceId="w" />);
    await act(async () => { finish("late private data"); await pending; });
    expect(localStorage.getItem("nlp-agent.imported-files.v1:alice:w")).toBeNull();
    expect(localStorage.getItem("nlp-agent.imported-files.v1:bob:w")).toBeNull();
  });

  it("exposes a recoverable recycle-bin view in the file manager", async () => {
    render(<FilesPanel workspaceId="workspace-1" />);

    fireEvent.click(screen.getByRole("button", { name: "回收站" }));
    await waitFor(() => expect(api.listStorageTrash).toHaveBeenCalledWith("workspace-1"));
    expect(screen.getByText("回收站为空")).toBeInTheDocument();
  });

  it("opens a server file in the same document preview used by learning imports", async () => {
    vi.mocked(api.listStorageFiles).mockResolvedValue({
      items: [{ id: "file-1", kind: "file", name: "lesson.md", mime_type: "text/markdown", size_bytes: 18, created_at: "2026-10-10T06:00:00Z", updated_at: "2026-10-10T06:15:00Z" }],
    });
    vi.mocked(api.readStorageFile).mockResolvedValue({
      content: "# 来自我的文件\n\n这是服务器文件内容。",
      truncated: false,
      mime_type: "text/markdown",
      bytes_read: 18,
    });

    render(<FilesPanel workspaceId="workspace-1" />);
    const fileButton = await screen.findByRole("button", { name: "查看 lesson.md" });
    expect(screen.getByText(/修改于/)).toBeInTheDocument();
    fireEvent.click(fileButton);

    expect(screen.getByRole("tab", { name: "学习文档导入" })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(api.readStorageFile).toHaveBeenCalledWith("file-1", "workspace-1"));
    const preview = await screen.findByTestId("markdown-preview");
    expect(preview).toHaveTextContent("来自我的文件");
    expect(preview).toHaveTextContent("这是服务器文件内容。");

    fireEvent.click(screen.getByRole("tab", { name: "我的文件" }));
    fireEvent.click(screen.getByRole("tab", { name: "学习文档导入" }));
    expect(screen.getByTestId("markdown-preview")).toBeVisible();
  });

  it("treats legacy storage timestamps without an offset as UTC", () => {
    expect(parseUtcTimestamp("2026-10-10T06:15:00").toISOString()).toBe("2026-10-10T06:15:00.000Z");
  });

  it.each([
    { content: "这是群发验证文档。", bytes: 27, expected: "这是群发验证文档。" },
    { content: "", bytes: 0, expected: "此文件为空（0 B）。" },
  ])("opens an accepted file in its actual recipient workspace ($bytes B)", async ({ content, bytes, expected }) => {
    const transfer = {
      id: "transfer-1", status: "accepted" as const, file_name: "测试验证文档.txt", size_bytes: 0,
      accepted_file_id: "received-copy", recipient_workspace_id: "personal-space",
    };
    vi.mocked(api.listFileTransfers).mockImplementation(async (box) => ({ items: [
      box === "outgoing" ? { ...transfer, recipient_workspace_id: null } : transfer,
    ] }));
    vi.mocked(api.readStorageFile).mockResolvedValue({ content, bytes_read: bytes, truncated: false, mime_type: "text/plain" });

    render(<FilesPanel workspaceId="classroom-space" />);
    fireEvent.click(screen.getByRole("button", { name: "消息" }));
    const download = await screen.findByRole("link", { name: "下载 测试验证文档.txt" });
    expect(download).toHaveAttribute("href", "/api/v1/storage/files/received-copy/download?workspace_id=personal-space");
    expect(download).toHaveAttribute("download");

    fireEvent.click(screen.getByRole("button", { name: "已发送" }));
    await screen.findByText("测试验证文档.txt");
    expect(screen.queryByRole("link", { name: "下载 测试验证文档.txt" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "收到的" }));
    fireEvent.click(await screen.findByRole("button", { name: "查看 测试验证文档.txt" }));

    expect(await screen.findByText(expected)).toBeInTheDocument();
    expect(api.readStorageFile).toHaveBeenCalledWith("received-copy", "personal-space");
    expect(screen.queryByText("来自知识教材")).not.toBeInTheDocument();
  });

function markdownFile(name = "notes.md", content = "# 学习笔记") {
  return new File([content], name, { type: "text/markdown" });
}

function pythonFile() {
  return new File(["print('hello')"], "demo.py", { type: "text/x-python" });
}

function upload(files: File[]) {
  fireEvent.click(screen.getByRole("tab", { name: "学习文档导入" }));
  fireEvent.change(screen.getByLabelText("选择本地文件"), { target: { files } });
}

  it("imports markdown and code files, previews them, and persists under a scoped key", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<FilesPanel userId="alice" workspaceId="workspace-1" />);
    upload([markdownFile(), pythonFile()]);

    expect(await screen.findByRole("button", { name: "预览 demo.py" })).toBeInTheDocument();
    expect(screen.getByTestId("code-preview")).toHaveTextContent("print('hello')");
    expect(screen.getByTestId("code-preview")).toHaveAttribute("data-language", "python");

    await user.click(screen.getByRole("button", { name: "预览 notes.md" }));
    expect(await screen.findByTestId("markdown-preview")).toHaveTextContent("# 学习笔记");

    const scopedValue = localStorage.getItem("nlp-agent.imported-files.v1:alice:workspace-1");
    expect(scopedValue).toBeTruthy();
    expect(JSON.parse(scopedValue!)).toHaveLength(2);

    unmount();
    render(<FilesPanel userId="alice" workspaceId="workspace-1" />);
    fireEvent.click(screen.getByRole("tab", { name: "学习文档导入" }));
    expect(screen.getByRole("button", { name: "预览 notes.md" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "预览 demo.py" })).toBeInTheDocument();
  });

  it("previews a published textbook file inside the file tool", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, text: async () => "# 教材内容" });
    vi.stubGlobal("fetch", fetchMock);

    render(<FilesPanel
      userId="alice"
      workspaceId="workspace-1"
      previewRequest={{
        id: "book-file-1",
        name: "教材.md",
        url: "/api/v1/learning/book/workspace-1/files/book-file-1",
        mediaType: "text/markdown",
        bytes: 20,
      }}
    />);
    fireEvent.click(screen.getByRole("tab", { name: "学习文档导入" }));

    expect(await screen.findByText("来自知识教材")).toBeInTheDocument();
    expect(await screen.findByTestId("markdown-preview")).toHaveTextContent("# 教材内容");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/learning/book/workspace-1/files/book-file-1",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("switches from a stored-file preview to a newer textbook preview request", async () => {
    vi.mocked(api.listStorageFiles).mockResolvedValue({
      items: [{ id: "file-1", kind: "file", name: "我的文件.md", mime_type: "text/markdown", size_bytes: 12, created_at: null, updated_at: null }],
    });
    vi.mocked(api.readStorageFile).mockResolvedValue({
      content: "# 我的文件预览",
      truncated: false,
      mime_type: "text/markdown",
      bytes_read: 12,
    });
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, text: async () => "# 新教材预览" });
    vi.stubGlobal("fetch", fetchMock);
    const firstRequest = {
      id: "book-file-1",
      name: "旧教材.md",
      url: "/api/v1/learning/book/workspace-1/files/book-file-1",
      mediaType: "text/markdown",
      bytes: 12,
    };
    const secondRequest = {
      id: "book-file-2",
      name: "新教材.md",
      url: "/api/v1/learning/book/workspace-1/files/book-file-2",
      mediaType: "text/markdown",
      bytes: 12,
    };

    const view = render(<FilesPanel workspaceId="workspace-1" previewRequest={firstRequest} />);
    fireEvent.click(screen.getByRole("tab", { name: "我的文件" }));
    fireEvent.click(await screen.findByRole("button", { name: "查看 我的文件.md" }));
    expect(await screen.findByText("# 我的文件预览")).toBeInTheDocument();

    view.rerender(<FilesPanel workspaceId="workspace-1" previewRequest={secondRequest} />);

    expect(await screen.findByText("# 新教材预览")).toBeInTheDocument();
    expect(screen.getByText("# 我的文件预览")).not.toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith(secondRequest.url, expect.objectContaining({ credentials: "include" }));
  });

  it("keeps only plain-text previews for text files", async () => {
    render(<FilesPanel userId="alice" workspaceId="workspace-1" />);
    upload([new File(["第一行"], "notes.txt", { type: "text/plain" })]);
    await waitFor(() => expect(screen.getAllByText("notes.txt")).not.toHaveLength(0));
    expect(screen.getByText("第一行")).toBeInTheDocument();
    expect(screen.queryByTestId("markdown-preview")).not.toBeInTheDocument();
    expect(screen.queryByTestId("code-preview")).not.toBeInTheDocument();
  });

  it("isolates files by user and workspace", async () => {
    const first = render(<FilesPanel userId="alice" workspaceId="workspace-1" />);
    upload([markdownFile("alice-ws1.md")]);
    expect(await screen.findByRole("button", { name: "预览 alice-ws1.md" })).toBeInTheDocument();
    first.unmount();

    const bob = render(<FilesPanel userId="bob" workspaceId="workspace-1" />);
    expect(await screen.findByText("此文件夹为空")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "预览 alice-ws1.md" })).not.toBeInTheDocument();
    bob.unmount();

    const otherWorkspace = render(<FilesPanel userId="alice" workspaceId="workspace-2" />);
    expect(await screen.findByText("此文件夹为空")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "预览 alice-ws1.md" })).not.toBeInTheDocument();
    otherWorkspace.unmount();
  });

  it("removes a single file and clears the whole list", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<FilesPanel userId="alice" workspaceId="workspace-1" />);
    upload([markdownFile("one.md"), markdownFile("two.md")]);

    await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(2));
    await user.click(screen.getByRole("button", { name: "移除 one.md" }));
    expect(screen.queryByRole("button", { name: "预览 one.md" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "预览 two.md" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "清空" }));
    expect(screen.getByText("导入学习文档")).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem("nlp-agent.imported-files.v1:alice:workspace-1")!)).toEqual([]);
    unmount();
  });

  it("keeps a bounded preview for files larger than the file-size limit", async () => {
    const large = new File(["# 大文件开头"], "large.md", { type: "text/markdown" });
    Object.defineProperty(large, "size", { configurable: true, value: 5 * 1024 * 1024 });
    render(<FilesPanel userId="alice" workspaceId="workspace-1" />);
    upload([large]);

    expect(await screen.findByRole("button", { name: "预览 large.md" })).toBeInTheDocument();
    expect(screen.getByText(/5.0 MB/)).toBeInTheDocument();
    expect(screen.getByText(/仅预览前段/)).toBeInTheDocument();
    expect(localStorage.getItem("nlp-agent.imported-files.v1:alice:workspace-1")).toContain("# 大文件开头");
  });

  it("rejects unsupported file types through the import path shared by drag-and-drop", async () => {
    render(<FilesPanel userId="alice" workspaceId="workspace-1" />);
    const binary = new File(["binary"], "program.exe", { type: "application/octet-stream" });
    upload([binary]);

    expect(await screen.findByRole("alert")).toHaveTextContent("program.exe：不支持的格式");
    expect(screen.getByText("导入学习文档")).toBeInTheDocument();
    expect(localStorage.getItem("nlp-agent.imported-files.v1:alice:workspace-1")).toBeNull();
  });

  it("loads a supported file through the actual drop event", async () => {
    render(<FilesPanel userId="alice" workspaceId="workspace-1" />);
    fireEvent.click(screen.getByRole("tab", { name: "学习文档导入" }));
    const panels = screen.getAllByLabelText("文件工具");
    const panel = panels[panels.length - 1];
    const dropped = new File(["# 拖入文件"], "dropped.md", { type: "text/markdown" });
    fireEvent.drop(panel, { dataTransfer: { files: [dropped] } });

    expect(await screen.findByRole("button", { name: "预览 dropped.md" })).toBeInTheDocument();
    expect(screen.getByTestId("markdown-preview")).toHaveTextContent("# 拖入文件");
  });

  it("tracks nested drag-enter/leave events instead of flickering while crossing child elements", () => {
    render(<FilesPanel userId="alice" workspaceId="workspace-1" />);
    fireEvent.click(screen.getByRole("tab", { name: "学习文档导入" }));
    const panels = screen.getAllByLabelText("文件工具");
    const panel = panels[panels.length - 1];

    fireEvent.dragEnter(panel);
    fireEvent.dragEnter(panel);
    expect(panel).toHaveClass("dragging");
    fireEvent.dragLeave(panel);
    expect(panel).toHaveClass("dragging");
    fireEvent.dragLeave(panel);
    expect(panel).not.toHaveClass("dragging");
  });

  it("uses list/listitem semantics instead of a nested-button listbox", async () => {
    render(<FilesPanel userId="alice" workspaceId="workspace-1" />);
    upload([markdownFile()]);

    const list = await screen.findByRole("list", { name: "已导入文件" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);
    expect(within(list).getByRole("button", { name: "预览 notes.md" })).toBeInTheDocument();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("finds a recipient only after an exact identity ID lookup and sends the selected file", async () => {
    vi.mocked(api.listStorageFiles).mockResolvedValue({
      items: [{ id: "file-1", kind: "file", name: "lesson.md", mime_type: "text/markdown", size_bytes: 18, created_at: null, updated_at: null }],
    });
    vi.mocked(api.lookupTransferRecipient).mockResolvedValue({ identity_id: "NV7ABC234DEF567", display_name: "小明" });
    vi.mocked(api.preflightFileTransfer).mockResolvedValue({ can_receive: true, reason: null });
    vi.mocked(api.createFileTransfer).mockResolvedValue({ id: "transfer-1", status: "pending" });

    render(<FilesPanel workspaceId="workspace-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "发送 lesson.md" }));
    expect(screen.queryByText("小明")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("接收人身份 ID"), { target: { value: "nv7abc234def567" } });
    fireEvent.click(screen.getByRole("button", { name: "查询接收人" }));

    await waitFor(() => expect(api.lookupTransferRecipient).toHaveBeenCalledWith("nv7abc234def567"));
    expect(await screen.findByText("小明")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "确认发送" }));

    await waitFor(() => expect(api.createFileTransfer).toHaveBeenCalledWith(expect.objectContaining({ source_file_id: "file-1", recipient_identity_id: "NV7ABC234DEF567", workspace_id: "workspace-1", idempotency_key: expect.any(String) })));
  });

  it("lists multiple incoming requests together and lets the recipient accept one", async () => {
    vi.mocked(api.getFileTransferSummary).mockResolvedValue({ pending_count: 2, notification_version: 2 });
    vi.mocked(api.getFileTransferUnreadSummary).mockResolvedValue({ pending_count: 2, unseen_accepted_count: 0, unread_count: 2, notification_version: 2 });
    vi.mocked(api.listFileTransfers).mockResolvedValue({ items: [
      { id: "transfer-1", status: "pending", file_name: "a.md", size_bytes: 12, sender: { identity_id: "NV2AAAAAAAAAAAAA", display_name: "甲" } },
      { id: "transfer-2", status: "pending", file_name: "b.txt", size_bytes: 20, sender: { identity_id: "NV3BBBBBBBBBBBBB", display_name: "乙" } },
    ] });
    vi.mocked(api.acceptFileTransfer).mockResolvedValue({ id: "transfer-1", status: "accepted" });

    render(<FilesPanel workspaceId="workspace-1" />);
    fireEvent.click(await screen.findByRole("button", { name: /消息/ }));

    expect(await screen.findByText(/来自 甲/)).toBeVisible();
    expect(screen.getByText(/来自 乙/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "同意 a.md" }));
    await waitFor(() => expect(api.acceptFileTransfer).toHaveBeenCalledWith("transfer-1"));
  });

  it("allows teacher to single send file to recipient with direct delivery", async () => {
    vi.mocked(api.getCurrentUser).mockResolvedValue(mockUser(["teacher"]));
    vi.mocked(api.listStorageFiles).mockResolvedValue({ items: [{
      id: "file-1",
      name: "lesson.md",
      kind: "file",
      mime_type: "text/markdown",
      size_bytes: 42,
      created_at: null,
      updated_at: null,
    }] });
    vi.mocked(api.lookupTransferRecipient).mockResolvedValue({ identity_id: "NV7ABC234DEF567", display_name: "小明" });
    vi.mocked(api.preflightFileTransfer).mockResolvedValue({ can_receive: true, reason: "" });
    vi.mocked(api.distributeFile).mockResolvedValue({
      distribution_id: "dist-1",
      source_file_id: "file-1",
      file_name: "lesson.md",
      size_bytes: 42,
      target_type: "user",
      target_label: "用户：小明",
      direct_delivery: true,
      total_targets: 1,
      success_count: 1,
      failed_count: 0,
      failures: [],
      created_at: "2026-03-24T00:00:00Z",
    });

    render(<FilesPanel workspaceId="workspace-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "发送 lesson.md" }));
    expect(await screen.findByRole("button", { name: "单发（指定用户）" })).toBeVisible();
    expect(screen.getByRole("button", { name: "群发（选择对象 / 用户类型）" })).toBeVisible();

    fireEvent.change(screen.getByLabelText("接收人身份 ID 或用户名"), { target: { value: "nv7abc234def567" } });
    fireEvent.click(screen.getByRole("button", { name: "查询接收人" }));

    await waitFor(() => expect(api.lookupTransferRecipient).toHaveBeenCalledWith("nv7abc234def567"));
    expect(await screen.findByText("小明")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "确认发送" }));

    await waitFor(() => expect(api.distributeFile).toHaveBeenCalledWith({
      source_file_id: "file-1",
      target_type: "user",
      target_value: "NV7ABC234DEF567",
      direct_delivery: true,
      workspace_id: "workspace-1",
    }));
  });

  it.each([
    { targets: 30, sent: 30, failed: 0, notice: /30 人已送达/, mayRetry: false },
    { targets: 30, sent: 29, failed: 1, notice: /29 人已送达，1 人发送失败.*接收方存储空间不足/, mayRetry: false },
    { targets: 30, sent: 0, failed: 30, notice: /发送失败，30 人未收到.*接收方存储空间不足/, mayRetry: true },
    { targets: 0, sent: 0, failed: 0, notice: /没有符合条件的接收人/, mayRetry: true },
  ])("reports class broadcast results for $sent of $targets deliveries", async ({ targets, sent, failed, notice, mayRetry }) => {
    vi.mocked(api.getCurrentUser).mockResolvedValue(mockUser(["teacher"]));
    vi.mocked(api.listStorageFiles).mockResolvedValue({ items: [{
      id: "file-1",
      name: "lesson.md",
      kind: "file",
      mime_type: "text/markdown",
      size_bytes: 42,
      created_at: null,
      updated_at: null,
    }] });
    vi.mocked(api.distributeFile).mockResolvedValue({
      distribution_id: "dist-2",
      source_file_id: "file-1",
      file_name: "lesson.md",
      size_bytes: 42,
      target_type: "classroom",
      target_label: "班级：NLP自然语言处理1班",
      direct_delivery: true,
      total_targets: targets,
      success_count: sent,
      failed_count: failed,
      failures: failed ? [{ user_id: "student-1", display_name: "小明", identity_id: "NVTEST1234", reason: "接收方存储空间不足" }] : [],
      created_at: "2026-03-24T00:00:00Z",
    });

    render(<FilesPanel workspaceId="workspace-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "发送 lesson.md" }));
    expect(await screen.findByRole("button", { name: "群发（选择对象 / 用户类型）" })).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "群发（选择对象 / 用户类型）" }));
    expect(await screen.findByText("全体学生 (42人)")).toBeVisible();
    expect(screen.getByText("指定班级")).toBeVisible();

    fireEvent.click(screen.getByLabelText("指定班级"));
    expect(screen.getByText("NLP自然语言处理1班 (30人)")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "确认群发" }));

    await waitFor(() => expect(api.distributeFile).toHaveBeenCalledWith({
      source_file_id: "file-1",
      target_type: "classroom",
      target_value: "class-1",
      direct_delivery: true,
      workspace_id: "workspace-1",
    }));
    expect(await screen.findByRole("alert")).toHaveTextContent(notice);
    const confirm = screen.getByRole("button", { name: "确认群发" });
    if (mayRetry) expect(confirm).toBeEnabled();
    else expect(confirm).toBeDisabled();
  });

  it("shows red unread badge count for unseen delivered files and marks them seen on opening messages", async () => {
    vi.mocked(api.markFileTransfersSeen).mockClear();
    vi.mocked(api.getFileTransferUnreadSummary)
      .mockResolvedValueOnce({ pending_count: 0, unseen_accepted_count: 3, unread_count: 3, notification_version: 5 })
      .mockResolvedValue({ pending_count: 0, unseen_accepted_count: 0, unread_count: 0, notification_version: 5 });
    vi.mocked(api.markFileTransfersSeen).mockResolvedValue({ ok: true, updated: 3 });
    vi.mocked(api.listFileTransfers).mockResolvedValue({
      items: [
        {
          id: "t-unread",
          status: "accepted",
          file_name: "新资料.pdf",
          size_bytes: 100,
          accepted_file_id: "f-new",
          recipient_workspace_id: "workspace-1",
          is_unread: true,
        },
      ],
    });

    render(<FilesPanel workspaceId="workspace-1" />);

    const msgBtn = await screen.findByRole("button", { name: /消息/ });
    expect(await within(msgBtn).findByText("3")).toHaveClass("unread-red-badge");

    fireEvent.click(msgBtn);

    expect(await screen.findByLabelText("未读")).toBeVisible();
    await waitFor(() => expect(api.markFileTransfersSeen).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(within(msgBtn).queryByText("3")).not.toBeInTheDocument());
  });
});
