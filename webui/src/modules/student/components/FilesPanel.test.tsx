import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FilesPanel } from "./FilesPanel";

vi.mock("./DocumentCodeView", () => ({
  DocumentCodeView: ({ code, language }: { code: string; language: string }) => <pre data-testid="code-preview" data-language={language}>{code}</pre>,
}));
vi.mock("./MarkdownContent", () => ({
  MarkdownContent: ({ children }: { children: string }) => <div data-testid="markdown-preview">{children}</div>,
}));

function markdownFile(name = "notes.md", content = "# 学习笔记") {
  return new File([content], name, { type: "text/markdown" });
}

function pythonFile() {
  return new File(["print('hello')"], "demo.py", { type: "text/x-python" });
}

function upload(files: File[]) {
  fireEvent.change(screen.getByLabelText("选择本地文件"), { target: { files } });
}

afterEach(() => {
  localStorage.clear();
});

describe("FilesPanel", () => {
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
    expect(screen.getByRole("button", { name: "预览 notes.md" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "预览 demo.py" })).toBeInTheDocument();
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
    expect(screen.getByText("导入学习文档")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "预览 alice-ws1.md" })).not.toBeInTheDocument();
    bob.unmount();

    const otherWorkspace = render(<FilesPanel userId="alice" workspaceId="workspace-2" />);
    expect(screen.getByText("导入学习文档")).toBeInTheDocument();
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
    const panel = screen.getByLabelText("文件工具");
    const dropped = new File(["# 拖入文件"], "dropped.md", { type: "text/markdown" });
    fireEvent.drop(panel, { dataTransfer: { files: [dropped] } });

    expect(await screen.findByRole("button", { name: "预览 dropped.md" })).toBeInTheDocument();
    expect(screen.getByTestId("markdown-preview")).toHaveTextContent("# 拖入文件");
  });

  it("tracks nested drag-enter/leave events instead of flickering while crossing child elements", () => {
    render(<FilesPanel userId="alice" workspaceId="workspace-1" />);
    const panel = screen.getByLabelText("文件工具");

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
