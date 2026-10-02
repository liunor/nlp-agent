import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
    listStorageTrash: vi.fn(),
    restoreStorageFile: vi.fn(),
    permanentlyDeleteStorageFile: vi.fn(),
  },
  storageFileDownloadUrl: (fileId: string) => `/api/v1/storage/files/${fileId}/download`,
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

describe("FilesPanel", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.mocked(api.getStorageUsage).mockResolvedValue(usage);
    vi.mocked(api.listStorageFiles).mockResolvedValue({ items: [] });
    vi.mocked(api.listStorageTrash).mockResolvedValue({ items: [] });
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

  it("keeps the learning-document importer behind a separate tab", async () => {
    render(<FilesPanel workspaceId="workspace-1" />);

    fireEvent.click(screen.getByRole("tab", { name: "学习文档导入" }));
    expect(screen.getByText("导入学习文档")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "我的文件" }));
    await waitFor(() => expect(screen.getByText("此文件夹为空")).toBeInTheDocument());
  });

  it("exposes a recoverable recycle-bin view in the file manager", async () => {
    render(<FilesPanel workspaceId="workspace-1" />);

    fireEvent.click(screen.getByRole("button", { name: "回收站" }));
    await waitFor(() => expect(api.listStorageTrash).toHaveBeenCalledWith("workspace-1"));
    expect(screen.getByText("回收站为空")).toBeInTheDocument();
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
    const pdf = new File(["%PDF"], "manual.pdf", { type: "application/pdf" });
    upload([pdf]);

    expect(await screen.findByRole("alert")).toHaveTextContent("manual.pdf：不支持的格式");
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
});
