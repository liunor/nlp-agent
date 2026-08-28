import { fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";

import { Sidebar } from "./Sidebar";

const props: ComponentProps<typeof Sidebar> = {
  sessions: [{ session_id: "session_1", user_id: "student", workspace_id: "default", channel: "web" }],
  preferences: { version: 2, context: { topic_id: null, topic_name: "", level: "beginner", mode: "explain" }, categories: [{ id: "category_1", name: "注意力机制", createdAt: 1 }], sessions: { session_1: { title: "Attention 入门", categoryId: "category_1" } } },
  activeId: "session_1", open: true, collapsed: false, connected: true,
  onClose: vi.fn(), onCollapse: vi.fn(), onExpand: vi.fn(), onSelect: vi.fn(), onCreate: vi.fn(), onMeta: vi.fn(), onAddCategory: vi.fn(() => "category_2"), onRenameCategory: vi.fn(), onDeleteCategory: vi.fn(), onDelete: vi.fn(), onAccount: vi.fn(), onSettings: vi.fn(),
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

  it("groups pinned sessions first across categories and restores source order after unpinning", () => {
    const sessions = [
      { session_id: "ordinary", user_id: "student", workspace_id: "default", channel: "web" },
      { session_id: "pinned_old", user_id: "student", workspace_id: "default", channel: "web" },
      { session_id: "pinned_new", user_id: "student", workspace_id: "default", channel: "web" },
    ];
    const preferences = {
      ...props.preferences,
      categories: [
        { id: "category_1", name: "注意力机制", createdAt: 1 },
        { id: "category_2", name: "语言模型", createdAt: 2 },
      ],
      sessions: {
        ordinary: { title: "普通会话" },
        pinned_old: { title: "较早置顶", categoryId: "category_1", pinnedAt: 100 },
        pinned_new: { title: "最近置顶", categoryId: "category_2", pinnedAt: 200 },
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
          pinned_old: { title: "较早置顶", categoryId: "category_1" },
          pinned_new: { title: "最近置顶", categoryId: "category_2" },
        },
      }}
    />);

    expect(visibleTitles()).toEqual(["普通会话", "较早置顶", "最近置顶"]);
    expect(screen.queryByRole("heading", { name: "置顶" })).not.toBeInTheDocument();
  });

  it("keeps unpinned category groups in the original session order", () => {
    const sessions = [
      { session_id: "category_2_session", user_id: "student", workspace_id: "default", channel: "web" },
      { session_id: "category_1_session", user_id: "student", workspace_id: "default", channel: "web" },
    ];
    const preferences = {
      ...props.preferences,
      categories: [
        { id: "category_1", name: "注意力机制", createdAt: 1 },
        { id: "category_2", name: "语言模型", createdAt: 2 },
      ],
      sessions: {
        category_1_session: { title: "注意力机制会话", categoryId: "category_1" },
        category_2_session: { title: "语言模型会话", categoryId: "category_2" },
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
  const onMeta = vi.fn();
  const { container } = render(<Sidebar {...props} onMeta={onMeta} />);

  fireEvent.click(container.querySelector(".session-menu summary")!);
  fireEvent.click(container.querySelector(".session-menu button")!);

  const input = container.querySelector<HTMLInputElement>(".session-rename-input")!;

  expect(input).toHaveValue("Attention 入门");

  fireEvent.change(input, { target: { value: "Transformer 学习" } });
  fireEvent.keyDown(input, { key: "Enter" });

  expect(onMeta).toHaveBeenCalledWith("session_1", {
    title: "Transformer 学习",
  });

  expect(container.querySelector(".session-rename-input")).not.toBeInTheDocument();
});
});
