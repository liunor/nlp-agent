import { fireEvent, render, screen, within } from "@testing-library/react";

import { TextInputDialog } from "./TextInputDialog";

describe("TextInputDialog", () => {
  it("submits a trimmed value through the application dialog", () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();
    render(<TextInputDialog open title="编辑教材知识点" description="修改目录名称。" label="知识点名称" initialValue="旧名称" onConfirm={onConfirm} onClose={onClose} />);

    const dialog = screen.getByRole("dialog", { name: "编辑教材知识点" });
    const input = within(dialog).getByRole("textbox", { name: "知识点名称" });
    fireEvent.change(input, { target: { value: "  新名称  " } });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存修改" }));

    expect(onConfirm).toHaveBeenCalledWith("新名称");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("closes without submitting when Escape is pressed", () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();
    render(<TextInputDialog open title="新建教材主题" description="新增目录主题。" label="主题名称" onConfirm={onConfirm} onClose={onClose} />);

    fireEvent.keyDown(window, { key: "Escape" });

    expect(onClose).toHaveBeenCalledOnce();
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
