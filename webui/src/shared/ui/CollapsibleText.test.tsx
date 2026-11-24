import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CollapsibleText } from "./CollapsibleText";

describe("CollapsibleText", () => {
  it("renders short text without toggle button", () => {
    render(<CollapsibleText text="简短的问题内容" maxLength={50} />);
    expect(screen.getByText("简短的问题内容")).toBeVisible();
    expect(screen.queryByRole("button", { name: /展开全文/ })).toBeNull();
  });

  it("collapses long text and toggles expanded state on click", () => {
    const longText = "这是一段非常长的学术论文摘要内容，包含了大量的自然语言处理算法细节与评测标准，长度超过了折叠阈值限制，需要折叠展示。".repeat(3);
    render(<CollapsibleText text={longText} maxLength={60} maxLines={3} />);

    const expandBtn = screen.getByRole("button", { name: /展开全文/ });
    expect(expandBtn).toBeVisible();

    // Click to expand
    fireEvent.click(expandBtn);
    const collapseBtn = screen.getByRole("button", { name: /收起/ });
    expect(collapseBtn).toBeVisible();

    // Click to collapse
    fireEvent.click(collapseBtn);
    expect(screen.getByRole("button", { name: /展开全文/ })).toBeVisible();
  });
});
