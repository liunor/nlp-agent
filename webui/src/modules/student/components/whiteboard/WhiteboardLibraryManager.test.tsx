import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { WhiteboardLibraryItem } from "@/shared/types";

import { api } from "@/platform/http/api";
import { WhiteboardLibraryManager } from "./WhiteboardLibraryManager";

vi.mock("@/platform/http/api", () => ({
  api: {
    renameWhiteboardLibraryItem: vi.fn(),
    deleteWhiteboardLibraryItem: vi.fn(),
  },
}));

const item: WhiteboardLibraryItem = {
  id: "asset-1",
  asset_code: "WB-000001",
  status: "published",
  created: 1,
  name: "注意力计算过程",
  elements: [{ id: "element-1", type: "rectangle" }],
};

describe("WhiteboardLibraryManager", () => {
  beforeEach(() => {
    vi.mocked(api.renameWhiteboardLibraryItem).mockResolvedValue({ item: { ...item, name: "新的素材名" } });
    vi.mocked(api.deleteWhiteboardLibraryItem).mockResolvedValue(undefined);
  });

  it("renames a material and exposes its stable code", async () => {
    const onItemsChange = vi.fn();
    render(<WhiteboardLibraryManager items={[item]} onItemsChange={onItemsChange} />);

    expect(screen.getByText(/WB-000001/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重命名 注意力计算过程" }));
    const input = await screen.findByRole("textbox", { name: "素材名称" });
    fireEvent.change(input, { target: { value: "新的素材名" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() => expect(api.renameWhiteboardLibraryItem).toHaveBeenCalledWith("asset-1", "新的素材名"));
    expect(onItemsChange).toHaveBeenCalledWith([{ ...item, name: "新的素材名" }]);
  });

  it("deletes a material after confirmation", async () => {
    const onItemsChange = vi.fn();
    vi.stubGlobal("confirm", vi.fn(() => true));
    render(<WhiteboardLibraryManager items={[item]} onItemsChange={onItemsChange} />);

    fireEvent.click(screen.getByRole("button", { name: "删除 注意力计算过程" }));

    await waitFor(() => expect(api.deleteWhiteboardLibraryItem).toHaveBeenCalledWith("asset-1"));
    expect(onItemsChange).toHaveBeenCalledWith([]);
  });

  it("shows the server reference-protection message when deletion is blocked", async () => {
    vi.mocked(api.deleteWhiteboardLibraryItem).mockRejectedValue(new Error("该素材正在被 2 个教材页面使用，请先移除教材引用后再删除。"));
    vi.stubGlobal("confirm", vi.fn(() => true));
    render(<WhiteboardLibraryManager items={[item]} onItemsChange={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "删除 注意力计算过程" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("2 个教材页面");
    expect(alert).toHaveTextContent("WB-000001");
    expect(alert).toHaveTextContent("Ctrl+F");
    expect(alert).toHaveTextContent("保存草稿、发布");
  });

  it("reveals a visual preview while hovering a material", () => {
    render(<WhiteboardLibraryManager items={[item]} onItemsChange={vi.fn()} />);

    const row = screen.getByText("注意力计算过程").closest("li");
    if (!row) throw new Error("material row was not rendered");
    fireEvent.mouseEnter(row);

    expect(screen.getByRole("img", { name: "预览：注意力计算过程" })).toBeVisible();
    expect(screen.getByRole("img", { name: "预览：注意力计算过程" }).parentElement).toHaveStyle({ position: "fixed" });
  });

  it("keeps the fixed preview aligned when the page scrolls", () => {
    render(<WhiteboardLibraryManager items={[item]} onItemsChange={vi.fn()} />);

    const row = screen.getByText("注意力计算过程").closest("li");
    if (!row) throw new Error("material row was not rendered");
    let top = 20;
    vi.spyOn(row, "getBoundingClientRect").mockImplementation(() => ({
      top,
      bottom: top + 40,
      left: 30,
      right: 300,
      width: 270,
      height: 40,
      x: 30,
      y: top,
      toJSON: () => ({}),
    }));

    fireEvent.mouseEnter(row);
    const preview = screen.getByRole("img", { name: "预览：注意力计算过程" }).parentElement;
    if (!preview) throw new Error("preview was not rendered");
    expect(preview).toHaveStyle({ top: "68px" });

    top = 620;
    fireEvent.scroll(window);
    expect(preview).toHaveStyle({ top: "442px" });
  });

  it("can collapse the material list without losing the catalog heading", () => {
    render(<WhiteboardLibraryManager items={[item]} onItemsChange={vi.fn()} />);

    const toggle = screen.getByRole("button", { name: /白板素材库/ });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("注意力计算过程")).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("注意力计算过程")).toBeInTheDocument();
  });
});
