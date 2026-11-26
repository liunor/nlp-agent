import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { AttachmentCard } from "./AttachmentCard";

it("shows compact file metadata without redundant ready-state text", () => {
  const remove = vi.fn();
  render(<AttachmentCard name="报告.docx" mediaType="application/octet-stream" url="/file" size={2048} status="ready" onRemove={remove} onRetry={vi.fn()} />);
  expect(screen.getByText("WORD · 2.0 KB")).toBeInTheDocument();
  expect(screen.queryByText("已就绪")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "移除附件 报告.docx" }));
  expect(remove).toHaveBeenCalledTimes(1);
});
it("uses filename-based thumbnails when storage MIME is generic", () => {
  render(<AttachmentCard name="图片.png" mediaType="application/octet-stream" url="/file/view" size={512} status="ready" onRemove={vi.fn()} onRetry={vi.fn()} />);
  expect(screen.getByRole("img", { name: "图片.png" })).toHaveAttribute("src", "/file/view");
});
