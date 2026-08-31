import { fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";

import { Sidebar } from "./Sidebar";

const props: ComponentProps<typeof Sidebar> = {
  sessions: [{ session_id: "session_1", user_id: "student", workspace_id: "default", channel: "web", title: "Attention 入门" }],
  preferences: { version: 2, context: { topic_id: null, topic_name: "", level: "beginner", mode: "explain" }, categories: [{ id: "category_1", name: "注意力机制", createdAt: 1 }], sessions: { session_1: { categoryId: "category_1" } } },
  activeId: "session_1", open: true, collapsed: false, connected: true,
  onClose: vi.fn(), onCollapse: vi.fn(), onExpand: vi.fn(), onSelect: vi.fn(), onCreate: vi.fn(), onRename: vi.fn(), onMeta: vi.fn(), onAddCategory: vi.fn(() => "category_2"), onRenameCategory: vi.fn(), onDeleteCategory: vi.fn(), onDelete: vi.fn(), onAccount: vi.fn(), onSettings: vi.fn(),
};

describe("Sidebar delete requests", () => {
  it("uses the Nova brand mark", () => {
    const { container } = render(<Sidebar {...props} />);

    expect(container.querySelector(".brand-mark img")).toHaveAttribute("src", expect.stringContaining("nova-remove"));
  });

  it("places account management below settings in the sidebar footer", () => {
    const { container } = render(<Sidebar {...props} />);

    const footerButtons = Array.from(container.querySelectorAll(".sidebar-footer .side-action"));
    expect(footerButtons.map((button) => button.getAttribute("aria-label"))).toEqual(["settings", "账户管理"]);
    fireEvent.click(screen.getByRole("button", { name: "账户管理" }));
    expect(props.onAccount).toHaveBeenCalledTimes(1);
  });

  it("delegates session and category deletion to the shared confirmation owner", () => {
    render(<Sidebar {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(props.onDelete).toHaveBeenCalledWith(
  "session_1",
  "Attention 入门",
  undefined,
);

    fireEvent.click(screen.getByRole("button", { name: "删除分类" }));
    expect(props.onDeleteCategory).toHaveBeenCalledWith("category_1", "注意力机制");
  });

  it("expands when the collapsed rail is clicked", () => {
    const onExpand = vi.fn();
    const { container } = render(<Sidebar {...props} open={false} collapsed onExpand={onExpand} />);

    fireEvent.click(container.querySelector("aside")!);

    expect(onExpand).toHaveBeenCalledTimes(1);
  });

  it("keeps the new-chat action usable while the sidebar rail is collapsed", () => {
    const onCreate = vi.fn();
    const onExpand = vi.fn();
    render(<Sidebar {...props} open={false} collapsed onCreate={onCreate} onExpand={onExpand} />);

    fireEvent.click(screen.getByRole("button", { name: "newChat" }));

    expect(onCreate).toHaveBeenCalledTimes(1);
    expect(onExpand).not.toHaveBeenCalled();
  });

  it("creates a category through the custom dialog without using a native prompt", () => {
    const onAddCategory = vi.fn(() => "category_3");
    render(<Sidebar {...props} onAddCategory={onAddCategory} />);

    fireEvent.click(screen.getByRole("button", { name: "newCategory" }));
    expect(screen.getByRole("dialog", { name: "新建分类" })).toBeVisible();
    expect(screen.getByRole("button", { name: "创建分类" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("分类名称"), { target: { value: "文本分类" } });
    fireEvent.click(screen.getByRole("button", { name: "创建分类" }));

    expect(onAddCategory).toHaveBeenCalledWith("文本分类");
    expect(screen.queryByRole("dialog", { name: "新建分类" })).not.toBeInTheDocument();
  });

  it("closes category creation with Escape without changing categories", () => {
    const onAddCategory = vi.fn();
    render(<Sidebar {...props} onAddCategory={onAddCategory} />);

    fireEvent.click(screen.getByRole("button", { name: "newCategory" }));
    fireEvent.keyDown(window, { key: "Escape" });

    expect(screen.queryByRole("dialog", { name: "新建分类" })).not.toBeInTheDocument();
    expect(onAddCategory).not.toHaveBeenCalled();
  });

  it("renders the backend title when the session has no local metadata", () => {
    const sessions = [{ session_id: "session_2", user_id: "student", workspace_id: "default", channel: "web", title: "Transformer 模型讲解" }];
    render(<Sidebar {...props} sessions={sessions} preferences={{ ...props.preferences, sessions: {} }} />);

    expect(screen.getByText("Transformer 模型讲解")).toBeInTheDocument();
  });

  it("shows a legacy local rename when the backend has no title", () => {
    const sessions = [{ session_id: "session_3", user_id: "student", workspace_id: "default", channel: "web" }];
    render(<Sidebar {...props} sessions={sessions} preferences={{ ...props.preferences, sessions: { session_3: { title: "升级前改的标题" } } }} />);

    expect(screen.getByText("升级前改的标题")).toBeInTheDocument();
  });

  it("prefers a legacy manual title over a non-manual backend title", () => {
    const sessions = [{ session_id: "session_4", user_id: "student", workspace_id: "default", channel: "web", title: "LLM 生成的标题" }];
    render(<Sidebar {...props} sessions={sessions} preferences={{ ...props.preferences, sessions: { session_4: { title: "我手动改的标题" } } }} />);

    expect(screen.getByText("我手动改的标题")).toBeInTheDocument();
    expect(screen.queryByText("LLM 生成的标题")).not.toBeInTheDocument();
  });

  it("prefers a fresh manual backend title over a legacy local one", () => {
    const sessions = [{ session_id: "session_5", user_id: "student", workspace_id: "default", channel: "web", title: "后端手动标题", title_is_manual: true }];
    render(<Sidebar {...props} sessions={sessions} preferences={{ ...props.preferences, sessions: { session_5: { title: "旧本地标题" } } }} />);

    expect(screen.getByText("后端手动标题")).toBeInTheDocument();
    expect(screen.queryByText("旧本地标题")).not.toBeInTheDocument();
  });

  it("renames a session through the backend callback", () => {
    const onRename = vi.fn();
    const { container } = render(<Sidebar {...props} onRename={onRename} />);

    fireEvent.click(container.querySelector(".session-menu summary")!);
    fireEvent.click(screen.getByRole("button", { name: "重命名" }));

    const input = container.querySelector<HTMLInputElement>(".session-rename-input")!;
    fireEvent.change(input, { target: { value: "新的标题" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onRename).toHaveBeenCalledWith("session_1", "新的标题");
  });

  it("groups pinned sessions first across categories and restores source order after unpinning", () => {
    const sessions = [
      { session_id: "ordinary", user_id: "student", workspace_id: "default", channel: "web", title: "普通会话" },
      { session_id: "pinned_old", user_id: "student", workspace_id: "default", channel: "web", title: "较早置顶" },
      { session_id: "pinned_new", user_id: "student", workspace_id: "default", channel: "web", title: "最近置顶" },
    ];
    const preferences = {
      ...props.preferences,
      categories: [
        { id: "category_1", name: "注意力机制", createdAt: 1 },
        { id: "category_2", name: "语言模型", createdAt: 2 },
      ],
      sessions: {
        ordinary: {},
        pinned_old: { categoryId: "category_1", pinnedAt: 100 },
        pinned_new: { categoryId: "category_2", pinnedAt: 200 },
      },
    };
    const { container, rerender } = render(<Sidebar {...props} sessions={sessions} preferences={preferences} />);
    const visibleTitles = () => Array.from(container.querySelectorAll(".session-main span"), (node) => node.textContent);

    expect(visibleTitles()).toEqual(["最近置顶", "较早置顶", "普通会话"]);
    expect(screen.getByRole("heading", { name: "置顶" })).toBeVisible();

    rerender(<Sidebar
      {...props}
      sessions={sessions}
      preferences={{
        ...preferences,
        sessions: {
          ...preferences.sessions,
          pinned_old: { categoryId: "category_1" },
          pinned_new: { categoryId: "category_2" },
        },
      }}
    />);

    expect(visibleTitles()).toEqual(["普通会话", "较早置顶", "最近置顶"]);
    expect(screen.queryByRole("heading", { name: "置顶" })).not.toBeInTheDocument();
  });

  it("keeps unpinned category groups in the original session order", () => {
    const sessions = [
      { session_id: "category_2_session", user_id: "student", workspace_id: "default", channel: "web", title: "语言模型会话" },
      { session_id: "category_1_session", user_id: "student", workspace_id: "default", channel: "web", title: "注意力机制会话" },
    ];
    const preferences = {
      ...props.preferences,
      categories: [
        { id: "category_1", name: "注意力机制", createdAt: 1 },
        { id: "category_2", name: "语言模型", createdAt: 2 },
      ],
      sessions: {
        category_1_session: { categoryId: "category_1" },
        category_2_session: { categoryId: "category_2" },
      },
    };
    const { container } = render(<Sidebar {...props} sessions={sessions} preferences={preferences} />);

    expect(Array.from(container.querySelectorAll(".session-main span"), (node) => node.textContent)).toEqual([
      "语言模型会话",
      "注意力机制会话",
    ]);
  });

  it("offers pinning from the session menu", () => {
    const onMeta = vi.fn();
    const { container } = render(<Sidebar {...props} onMeta={onMeta} />);

    fireEvent.click(container.querySelector(".session-menu summary")!);
    fireEvent.click(screen.getByRole("button", { name: "置顶" }));

    expect(onMeta).toHaveBeenCalledWith("session_1", { pinnedAt: expect.any(Number) });
  });
  it("renames a session inline without using a native prompt", () => {
  const onRename = vi.fn();
  const { container } = render(<Sidebar {...props} onRename={onRename} />);

  fireEvent.click(container.querySelector(".session-menu summary")!);
  fireEvent.click(container.querySelector(".session-menu button")!);

  const input = container.querySelector<HTMLInputElement>(".session-rename-input")!;

  expect(input).toHaveValue("Attention 入门");

  fireEvent.change(input, { target: { value: "Transformer 学习" } });
  fireEvent.keyDown(input, { key: "Enter" });

  expect(onRename).toHaveBeenCalledWith("session_1", "Transformer 学习");

  expect(container.querySelector(".session-rename-input")).not.toBeInTheDocument();
});
});
