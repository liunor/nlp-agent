import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { TeacherBookEditor } from "./TeacherBookEditor";
import type { TeacherCatalog } from "@/shared/types";

const { getNavigationMock, getPageMock, updateCatalogMock, updatePageMock, previewImportMock, applyImportMock } = vi.hoisted(() => ({
  getNavigationMock: vi.fn(),
  getPageMock: vi.fn(),
  updateCatalogMock: vi.fn(),
  updatePageMock: vi.fn(),
  previewImportMock: vi.fn(),
  applyImportMock: vi.fn(),
}));

vi.mock("@/platform/http/api", () => ({
  api: {
    getTeacherBookNavigation: getNavigationMock,
    getTeacherBookPage: getPageMock,
    updateTeacherCatalog: updateCatalogMock,
    updateTeacherBookPage: updatePageMock,
    publishTeacherBookPage: vi.fn(),
    previewTeacherBookImport: previewImportMock,
    applyTeacherBookImport: applyImportMock,
    previewTeacherBookArchiveImport: vi.fn(),
    applyTeacherBookArchiveImport: vi.fn(),
  },
}));

describe("TeacherBookEditor Markdown authoring", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    updateCatalogMock.mockReset();
    updatePageMock.mockReset();
    updatePageMock.mockResolvedValue({
      page: {
        workspace_id: "workspace-1",
        topic_id: "pytorch",
        topic_name: "PyTorch 基础",
        knowledge_point_id: "tensor",
        title: "张量与形状",
        draft_markdown: "张量",
        published_markdown: "张量",
        revision: 2,
        published_revision: 1,
        updated_at: null,
      },
      warnings: [],
    });
    getNavigationMock.mockResolvedValue({
      workspace_id: "workspace-1",
      items: [{
        topic_id: "pytorch",
        topic_name: "PyTorch 基础",
        knowledge_point_id: "tensor",
        title: "张量与形状",
        sort_order: 0,
        topic_status: "enabled",
        knowledge_point_status: "enabled",
        has_draft: true,
        has_published: true,
        revision: 1,
        published_revision: 1,
      }],
    });
    getPageMock.mockResolvedValue({
      page: {
        workspace_id: "workspace-1",
        topic_id: "pytorch",
        topic_name: "PyTorch 基础",
        knowledge_point_id: "tensor",
        title: "张量与形状",
        draft_markdown: "张量",
        published_markdown: "张量",
        revision: 1,
        published_revision: 1,
        updated_at: null,
      },
    });
  });

  const catalog: TeacherCatalog = {
    workspace_id: "workspace-1",
    topics: [{ id: "pytorch", name: "PyTorch 基础", description: "", status: "enabled", knowledge_points: [{ id: "tensor", name: "张量与形状", markdown: "", status: "enabled", sort_order: 0 }] }],
    exercise_blueprints: [],
    review_blueprints: [],
    guided_blueprints: [],
  };

  it("offers a Markdown toolbar and applies bold formatting to the selection", async () => {
    render(<TeacherBookEditor workspaceId="workspace-1" />);
    const editor = await screen.findByRole("textbox", { name: "教材正文 Markdown" }) as HTMLTextAreaElement;

    fireEvent.change(editor, { target: { value: "张量" } });
    editor.setSelectionRange(0, 2);
    fireEvent.click(screen.getByRole("button", { name: "加粗" }));

    expect(editor).toHaveValue("**张量**");
  });

  it("supports the Ctrl+B shortcut without leaving source mode", async () => {
    render(<TeacherBookEditor workspaceId="workspace-1" />);
    const editor = await screen.findByRole("textbox", { name: "教材正文 Markdown" }) as HTMLTextAreaElement;

    fireEvent.change(editor, { target: { value: "重点" } });
    editor.setSelectionRange(0, 2);
    fireEvent.keyDown(editor, { key: "b", ctrlKey: true });

    await waitFor(() => expect(editor).toHaveValue("**重点**"));
    expect(screen.getByRole("textbox", { name: "教材正文 Markdown" })).toBeVisible();
  });

  it("supports Ctrl+Z and Ctrl+Y source editing history", async () => {
    render(<TeacherBookEditor workspaceId="workspace-1" />);
    const editor = await screen.findByRole("textbox", { name: "教材正文 Markdown" }) as HTMLTextAreaElement;

    fireEvent.change(editor, { target: { value: "第一版" } });
    fireEvent.change(editor, { target: { value: "第二版" } });
    fireEvent.keyDown(editor, { key: "z", ctrlKey: true });
    await waitFor(() => expect(editor).toHaveValue("第一版"));
    fireEvent.keyDown(editor, { key: "y", ctrlKey: true });
    await waitFor(() => expect(editor).toHaveValue("第二版"));
  });

  it("inserts an image reference at the editor cursor and keeps a local preview", async () => {
    render(<TeacherBookEditor workspaceId="workspace-1" />);
    const editor = await screen.findByRole("textbox", { name: "教材正文 Markdown" }) as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: "图片说明\n" } });
    editor.setSelectionRange(editor.value.length, editor.value.length);
    const image = new File([new Uint8Array([137, 80, 78, 71])], "diagram.png", { type: "image/png" });
    Object.defineProperty(image, "arrayBuffer", { configurable: true, value: async () => new Uint8Array([137, 80, 78, 71]).buffer });
    fireEvent.change(screen.getByLabelText("附加编辑图片"), { target: { files: [image] } });

    await waitFor(() => expect(editor).toHaveValue("图片说明\n![diagram.png](assets/diagram.png)"));
    expect(screen.getByRole("status")).toHaveTextContent("保存草稿时会自动入库并重写 Markdown 图片地址");
    expect(screen.queryByText("保存时会写入当前知识点的教材资源")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "预览正文" }));
    expect(screen.getByAltText("diagram.png")).toBeVisible();
    expect(screen.getByText("diagram.png")).toHaveClass("markdown-image-caption");
  });

  it("keeps an attached image preview after the asset is saved to the page", async () => {
    updatePageMock.mockImplementation((_workspaceId: string, _pointId: string, markdown: string, revision: number) => ({
      page: {
        workspace_id: "workspace-1", topic_id: "pytorch", topic_name: "PyTorch 基础", knowledge_point_id: "tensor", title: "张量与形状",
        draft_markdown: markdown.replace("assets/saved.png", "/api/v1/learning/book/workspace-1/assets/assets/saved.png"), published_markdown: "张量", revision: revision + 1, published_revision: 1, updated_at: null,
      },
      warnings: [],
    }));
    render(<TeacherBookEditor workspaceId="workspace-1" />);
    const editor = await screen.findByRole("textbox", { name: "教材正文 Markdown" }) as HTMLTextAreaElement;
    const image = new File([new Uint8Array([137, 80, 78, 71])], "saved.png", { type: "image/png" });
    Object.defineProperty(image, "arrayBuffer", { configurable: true, value: async () => new Uint8Array([137, 80, 78, 71]).buffer });
    fireEvent.change(screen.getByLabelText("附加编辑图片"), { target: { files: [image] } });

    await waitFor(() => expect(editor).toHaveValue("张量\n\n![saved.png](assets/saved.png)"));
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));

    await waitFor(() => expect(updatePageMock).toHaveBeenCalledWith(
      "workspace-1",
      "tensor",
      "张量\n\n![saved.png](assets/saved.png)",
      1,
      [expect.objectContaining({ asset_path: "assets/saved.png", media_type: "image/png" })],
    ));
    fireEvent.click(screen.getByRole("button", { name: "预览正文" }));
    expect(screen.getByAltText("saved.png")).toBeVisible();
  });

  it("imports an image path into Markdown and previews the persisted asset", async () => {
    previewImportMock.mockResolvedValue({ content_markdown: "# 导入页面\n\n![imported.png](assets/imported.png)", removed_frameworks: [], warnings: [] });
    applyImportMock.mockResolvedValue({ page: {
      workspace_id: "workspace-1", topic_id: "pytorch", topic_name: "PyTorch 基础", knowledge_point_id: "tensor", title: "张量与形状",
      draft_markdown: "# 导入页面\n\n![imported.png](/api/v1/learning/book/workspace-1/assets/assets/imported.png)", published_markdown: "张量", revision: 2, published_revision: 1, updated_at: null,
    } });
    render(<TeacherBookEditor workspaceId="workspace-1" />);
    await screen.findByRole("textbox", { name: "教材正文 Markdown" });
    const markdown = new File(["# 导入页面"], "lesson.md", { type: "text/markdown" });
    const image = new File([new Uint8Array([137, 80, 78, 71])], "imported.png", { type: "image/png" });
    Object.defineProperty(image, "arrayBuffer", { configurable: true, value: async () => new Uint8Array([137, 80, 78, 71]).buffer });
    fireEvent.change(screen.getByLabelText("导入 Markdown/图片"), { target: { files: [markdown, image] } });

    await screen.findByText("lesson.md");
    await userEvent.setup().click(screen.getByRole("button", { name: "应用到当前草稿" }));
    const editor = screen.getByRole("textbox", { name: "教材正文 Markdown" }) as HTMLTextAreaElement;
    await waitFor(() => expect(editor).toHaveValue("# 导入页面\n\n![imported.png](/api/v1/learning/book/workspace-1/assets/assets/imported.png)"));
    expect(applyImportMock).toHaveBeenCalledWith("workspace-1", "tensor", "lesson.md", "# 导入页面\n\n![imported.png](assets/imported.png)", 1, [expect.objectContaining({ asset_path: "assets/imported.png", media_type: "image/png" })]);
    await userEvent.setup().click(screen.getByRole("button", { name: "预览正文" }));
    expect(screen.getByAltText("imported.png")).toBeVisible();
  });

  it("collapses the left catalog without hiding the writing surface", async () => {
    render(<TeacherBookEditor workspaceId="workspace-1" />);

    expect(await screen.findByRole("button", { name: "收起教材目录" })).toBeVisible();
    expect(await screen.findByRole("textbox", { name: "教材正文 Markdown" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "收起教材目录" }));
    expect(screen.getByRole("button", { name: "展开教材目录" })).toBeVisible();
    expect(screen.getByRole("textbox", { name: "教材正文 Markdown" })).toBeVisible();
  });

  it("keeps an empty import row so the writing surface stays in the filling grid row", async () => {
    render(<TeacherBookEditor workspaceId="workspace-1" />);

    await screen.findByRole("textbox", { name: "教材正文 Markdown" });
    const editor = document.querySelector(".teacher-book-editor");
    const children = Array.from(editor?.children ?? []);

    expect(editor?.querySelector(".teacher-book-import-states")).toBeTruthy();
    expect(children[2]).toHaveClass("teacher-book-import-states");
    expect(children[3]).toHaveClass("teacher-book-layout");
  });

  it("manages catalog entries from inline menus and persists topic changes", async () => {
    const user = userEvent.setup();
    updateCatalogMock.mockResolvedValue({ catalog });
    render(<TeacherBookEditor workspaceId="workspace-1" catalog={catalog} onCatalogChange={vi.fn()} />);

    await screen.findByRole("textbox", { name: "教材正文 Markdown" });
    expect(screen.queryByText("编辑教材目录")).not.toBeInTheDocument();
    await user.click(screen.getByLabelText("教材目录选项"));
    await user.click(screen.getByRole("button", { name: "新增主题" }));
    const dialog = screen.getByRole("dialog", { name: "新建教材主题" });
    await user.type(within(dialog).getByRole("textbox", { name: "主题名称" }), "Transformer 基础");
    await user.click(within(dialog).getByRole("button", { name: "新增主题" }));

    await waitFor(() => expect(updateCatalogMock).toHaveBeenCalledWith("workspace-1", expect.objectContaining({ topics: expect.arrayContaining([expect.objectContaining({ name: "Transformer 基础" })]) })));
  });

  it("searches and collapses the existing catalog without leaving the directory", async () => {
    const user = userEvent.setup();
    render(<TeacherBookEditor workspaceId="workspace-1" catalog={catalog} onCatalogChange={vi.fn()} />);

    await screen.findByRole("textbox", { name: "教材正文 Markdown" });
    expect(screen.getByRole("button", { name: "展开主题 PyTorch 基础" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "张量与形状" })).not.toBeInTheDocument();
    const search = screen.getByRole("searchbox", { name: "搜索教材目录" });
    await user.type(search, "张量");
    expect(screen.getByRole("button", { name: "张量与形状" })).toBeVisible();
    await user.clear(search);

    expect(screen.getByRole("button", { name: "展开主题 PyTorch 基础" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "张量与形状" })).not.toBeInTheDocument();
  });

  it("hides status badges and sorts disabled entries after active entries", async () => {
    const statusCatalog: TeacherCatalog = {
      ...catalog,
      topics: [{
        ...catalog.topics[0],
        knowledge_points: [
          { ...catalog.topics[0].knowledge_points[0], status: "disabled" },
          { id: "published", name: "已发布知识点", markdown: "", status: "enabled", sort_order: 1 },
        ],
      }],
    };
    getNavigationMock.mockResolvedValueOnce({
      workspace_id: "workspace-1",
      items: [
        { topic_id: "pytorch", topic_name: "PyTorch 基础", knowledge_point_id: "tensor", title: "张量与形状", sort_order: 0, topic_status: "enabled", knowledge_point_status: "disabled", has_draft: true, has_published: true, revision: 1, published_revision: 1 },
        { topic_id: "pytorch", topic_name: "PyTorch 基础", knowledge_point_id: "published", title: "已发布知识点", sort_order: 1, topic_status: "enabled", knowledge_point_status: "enabled", has_draft: true, has_published: true, revision: 1, published_revision: 1 },
      ],
    });
    render(<TeacherBookEditor workspaceId="workspace-1" catalog={statusCatalog} onCatalogChange={vi.fn()} />);

    await screen.findByRole("textbox", { name: "教材正文 Markdown" });
    await userEvent.setup().click(screen.getByRole("button", { name: "展开主题 PyTorch 基础" }));
    expect(screen.queryByText("已停用")).not.toBeInTheDocument();
    expect(screen.queryByText("已发布")).not.toBeInTheDocument();
    const pointButtons = [...document.querySelectorAll<HTMLButtonElement>(".teacher-book-tree-point-main")];
    expect(pointButtons.map((button) => button.textContent?.trim())).toEqual(["已发布知识点", "张量与形状"]);
    expect(pointButtons[0]?.closest(".teacher-book-tree-point")).not.toHaveClass("is-disabled");
    expect(pointButtons[1]?.closest(".teacher-book-tree-point")).toHaveClass("is-disabled");
  });

  it("persists knowledge point edits from the point options menu", async () => {
    const user = userEvent.setup();
    updateCatalogMock.mockResolvedValue({ catalog });
    render(<TeacherBookEditor workspaceId="workspace-1" catalog={catalog} onCatalogChange={vi.fn()} />);

    await screen.findByRole("textbox", { name: "教材正文 Markdown" });
    await user.click(screen.getByRole("button", { name: "展开主题 PyTorch 基础" }));
    await user.click(screen.getByLabelText("张量与形状选项"));
    await user.click(screen.getByRole("button", { name: "编辑知识点" }));
    const dialog = screen.getByRole("dialog", { name: "编辑教材知识点" });
    const input = within(dialog).getByRole("textbox", { name: "知识点名称" });
    await user.clear(input);
    await user.type(input, "张量与形状（新版）");
    await user.click(within(dialog).getByRole("button", { name: "保存修改" }));

    await waitFor(() => expect(updateCatalogMock).toHaveBeenCalledWith("workspace-1", expect.objectContaining({ topics: [expect.objectContaining({ knowledge_points: [expect.objectContaining({ id: "tensor", name: "张量与形状（新版）" })] })] })));
  });

  it("requires confirmation in the shared dialog before deleting a knowledge point", async () => {
    const user = userEvent.setup();
    updateCatalogMock.mockResolvedValue({ catalog });
    render(<TeacherBookEditor workspaceId="workspace-1" catalog={catalog} onCatalogChange={vi.fn()} />);

    await screen.findByRole("textbox", { name: "教材正文 Markdown" });
    await user.click(screen.getByRole("button", { name: "展开主题 PyTorch 基础" }));
    await user.click(screen.getByLabelText("张量与形状选项"));
    await user.click(screen.getByRole("button", { name: "删除知识点" }));

    const dialog = screen.getByRole("alertdialog", { name: "删除知识点“张量与形状”？" });
    expect(dialog).toBeVisible();
    expect(updateCatalogMock).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("uses the same Markdown heading levels for the teacher outline and student page", async () => {
    const user = userEvent.setup();
    getPageMock.mockResolvedValueOnce({
      page: {
        workspace_id: "workspace-1", topic_id: "pytorch", topic_name: "PyTorch 基础", knowledge_point_id: "tensor", title: "张量与形状",
        draft_markdown: "# 张量与形状\n\n## 核心概念\n\n正文", published_markdown: "", revision: 1, published_revision: null, updated_at: null,
      },
    });
    render(<TeacherBookEditor workspaceId="workspace-1" />);

    await screen.findByRole("textbox", { name: "教材正文 Markdown" });
    await user.click(screen.getByRole("button", { name: "预览正文" }));
    expect(screen.getByRole("navigation", { name: "本页小标题" })).toBeVisible();
    expect(screen.getByRole("link", { name: "核心概念" })).toHaveAttribute("href", "#核心概念");
  });

  it("keeps page metadata and actions in a dedicated horizontal header", async () => {
    render(<TeacherBookEditor workspaceId="workspace-1" />);

    await screen.findByRole("textbox", { name: "教材正文 Markdown" });
    const heading = document.querySelector(".teacher-book-page-heading");
    expect(heading?.querySelector(".teacher-book-page-heading-info")).toBeTruthy();
    expect(heading?.querySelector(".teacher-book-page-actions")).toBeTruthy();
    expect(screen.queryByText(/教师教材/)).not.toBeInTheDocument();
  });
});
