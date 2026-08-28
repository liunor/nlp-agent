import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { LearningBookNavigationItem, LearningBookPage } from "@/shared/types";

import { api } from "@/platform/http/api";

import { KnowledgeBookPanel } from "./KnowledgeBookPanel";

vi.mock("@/platform/http/api", () => ({
  api: {
    getLearningBookNavigation: vi.fn(),
    getLearningBookPage: vi.fn(),
  },
}));

const navigation: LearningBookNavigationItem[] = [
  { topic_id: "topic-1", topic_name: "基础", knowledge_point_id: "point-1", title: "词法分析", sort_order: 1, revision: 1 },
  { topic_id: "topic-1", topic_name: "基础", knowledge_point_id: "point-2", title: "句法分析", sort_order: 2, revision: 1 },
];

const page: LearningBookPage = {
  workspace_id: "workspace-1",
  topic_id: "topic-1",
  topic_name: "基础",
  knowledge_point_id: "point-1",
  title: "词法分析",
  content_markdown: "## 核心概念\n\n词元是文本处理的基本单位。\n\n## 练习",
  revision: 1,
};

describe("KnowledgeBookPanel", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.history.replaceState({}, "", "/");
    vi.mocked(api.getLearningBookNavigation).mockResolvedValue({ workspace_id: "workspace-1", items: navigation });
    vi.mocked(api.getLearningBookPage).mockImplementation((_workspaceId, knowledgePointId) => Promise.resolve({ page: { ...page, knowledge_point_id: knowledgePointId, title: knowledgePointId === "point-2" ? "句法分析" : page.title } }));
  });

  afterEach(() => {
    window.history.replaceState({}, "", "/");
  });

  it("loads the published navigation and renders page headings for the right outline", async () => {
    render(<KnowledgeBookPanel workspaceId="workspace-1" />);

    expect(await screen.findByText("词法分析")).toBeInTheDocument();
    expect(await screen.findByText("词元是文本处理的基本单位。")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByRole("button", { name: "核心概念" }).length).toBeGreaterThan(0));
    expect(api.getLearningBookNavigation).toHaveBeenCalledWith("workspace-1");
    expect(api.getLearningBookPage).toHaveBeenCalledWith("workspace-1", "point-1");
  });

  it("lets Markdown own the article title without showing teacher-only metadata", async () => {
    vi.mocked(api.getLearningBookPage).mockResolvedValue({
      page: { ...page, content_markdown: "# 词法分析\n\n## 核心概念\n\n正文" },
    });
    render(<KnowledgeBookPanel workspaceId="workspace-1" />);

    expect(await screen.findByRole("heading", { name: "词法分析" })).toBeInTheDocument();
    expect(screen.queryByText("教师教材 · 第 1 节")).not.toBeInTheDocument();
    expect(screen.queryByText("基础", { selector: "header p" })).not.toBeInTheDocument();
  });

  it("shows an explicit empty state when the teacher has not published a page", async () => {
    vi.mocked(api.getLearningBookNavigation).mockResolvedValue({ workspace_id: "workspace-1", items: [] });

    render(<KnowledgeBookPanel workspaceId="workspace-1" />);

    expect(await screen.findByText("教师还没有发布知识教材。")).toBeInTheDocument();
    expect(screen.getByText("从左侧目录选择一个知识点开始阅读。")).toBeInTheDocument();
  });

  it("restores the last knowledge point when the reader is reopened", async () => {
    window.sessionStorage.setItem("nova:knowledge-book:workspace-1", JSON.stringify({ selectedId: "point-2", expandedTopics: ["topic-1"], scrollPositions: { "point-2": 120 }, leftCollapsed: true, rightCollapsed: false }));

    render(<KnowledgeBookPanel workspaceId="workspace-1" />);

    expect(await screen.findByRole("heading", { name: "句法分析" })).toBeInTheDocument();
    expect(api.getLearningBookPage).toHaveBeenCalledWith("workspace-1", "point-2");
  });

  it("offers an explicit Nova action for selected article text", async () => {
    const user = userEvent.setup();
    const askNova = vi.fn();
    render(<KnowledgeBookPanel workspaceId="workspace-1" onAskNova={askNova} />);

    const selectedText = await screen.findByText("词元是文本处理的基本单位。");
    const range = document.createRange();
    range.selectNodeContents(selectedText);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    fireEvent.pointerUp(selectedText);

    const askButton = await screen.findByRole("button", { name: "向 Nova 提问" });
    await user.click(askButton);
    expect(askNova).toHaveBeenCalledWith(expect.stringContaining("词元是文本处理的基本单位。"));
    expect(askNova).toHaveBeenCalledWith(expect.stringContaining("的「核心概念」小节"));
    expect(screen.queryByRole("button", { name: "向 Nova 提问" })).not.toBeInTheDocument();
  });

  it("hands Python lesson code to the sandbox callback", async () => {
    const user = userEvent.setup();
    const openInSandbox = vi.fn();
    vi.mocked(api.getLearningBookPage).mockResolvedValue({
      page: {
        ...page,
        content_markdown: "## 示例\n\n```python\nimport torch\nprint(torch.__version__)\n```",
      },
    });

    render(<KnowledgeBookPanel workspaceId="workspace-1" onOpenInSandbox={openInSandbox} />);

    await user.click(await screen.findByRole("button", { name: "在沙箱中打开" }));

    expect(openInSandbox).toHaveBeenCalledWith("import torch\nprint(torch.__version__)", "python");
  });

  it("opens a deep-linked point and filters the large outline", async () => {
    const user = userEvent.setup();
    window.history.replaceState({}, "", "/?tool=knowledge-book&bookPoint=point-2&bookHeading=核心概念");

    render(<KnowledgeBookPanel workspaceId="workspace-1" />);

    expect(await screen.findByRole("heading", { name: "句法分析" })).toBeInTheDocument();
    expect(window.location.search).toContain("bookPoint=point-2");
    const search = screen.getByRole("textbox", { name: "搜索主题或知识点" });
    await user.type(search, "句法");
    expect(screen.getAllByRole("button", { name: "句法分析" }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "词法分析" })).not.toBeInTheDocument();
  });

  it("writes the selected heading into the shareable URL", async () => {
    const user = userEvent.setup();
    render(<KnowledgeBookPanel workspaceId="workspace-1" />);

    await screen.findByRole("heading", { name: "词法分析" });
    await user.click(screen.getByRole("button", { name: "核心概念" }));

    expect(new URLSearchParams(window.location.search).get("bookHeading")).toBe("核心概念");
  });

  it("uses catalog sort order for previous and next navigation", async () => {
    const user = userEvent.setup();
    const unsortedNavigation = [
      { ...navigation[0], sort_order: 2 },
      { ...navigation[1], sort_order: 1 },
    ];
    vi.mocked(api.getLearningBookNavigation).mockResolvedValue({ workspace_id: "workspace-1", items: unsortedNavigation });
    render(<KnowledgeBookPanel workspaceId="workspace-1" />);

    await screen.findByRole("heading", { name: "句法分析" });
    expect(screen.getByRole("button", { name: "上一节" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "下一节" }));

    await waitFor(() => expect(api.getLearningBookPage).toHaveBeenCalledWith("workspace-1", "point-1"));
  });

  it("renders the explicit demo教材 with expanded topics and PyTorch code", async () => {
    vi.mocked(api.getLearningBookNavigation).mockClear();
    vi.mocked(api.getLearningBookPage).mockClear();
    window.history.replaceState({}, "", "/?tool=knowledge-book&bookDemo=1");

    render(<KnowledgeBookPanel workspaceId="workspace-1" />);

    expect(await screen.findByRole("heading", { name: "张量与批次" })).toBeInTheDocument();
    expect(screen.getByText("演示教材")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /1\. NLP 基础/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "分词与词表" })).toBeVisible();
    expect(await screen.findByText("import", { exact: true })).toBeVisible();
    expect(api.getLearningBookNavigation).not.toHaveBeenCalled();
    expect(api.getLearningBookPage).not.toHaveBeenCalled();
  });
});
