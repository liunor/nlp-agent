import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi, it, expect } from "vitest";
import { api } from "@/platform/http/api";
import { FileSpaceReader } from "./FileSpaceReader";

vi.mock("@/platform/http/api", () => ({ api: { readFileContent: vi.fn() } }));

it("reads document blocks with locations and continues with the same version", async () => {
  vi.mocked(api.readFileContent).mockResolvedValueOnce({
    content: "标题", blocks: [{ kind: "paragraph", number: 1, text: "标题", truncated: false, needs_ocr: false }],
    sha256: "abc", unit: "block", offset: 0, next_offset: 1, truncated: true, warnings: [],
  }).mockResolvedValueOnce({
    content: "结论", blocks: [{ kind: "paragraph", number: 2, text: "结论", truncated: false, needs_ocr: false }],
    sha256: "abc", unit: "block", offset: 1, next_offset: null, truncated: false, warnings: [],
  });
  render(<FileSpaceReader fileId="f" name="报告.docx" workspaceId="w" />);
  expect(await screen.findByText("标题")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "继续读取" }));
  expect(await screen.findByText("结论")).toBeInTheDocument();
  await waitFor(() => expect(api.readFileContent).toHaveBeenLastCalledWith("f", "w", 1, "abc", 0));
});

it("shows a failed image preview with download and retry actions", () => {
  const { rerender } = render(<FileSpaceReader fileId="image-1" name="图片.png" workspaceId="w" />);
  fireEvent.error(screen.getByRole("img", { name: "图片.png" }));
  expect(screen.getByRole("alert")).toHaveTextContent("图片预览失败");
  expect(screen.queryByRole("img", { name: "图片.png" })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "下载" })).toHaveAttribute("href", "/api/v1/storage/files/image-1/download?workspace_id=w");

  fireEvent.click(screen.getByRole("button", { name: "重试预览" }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  const image = screen.getByRole("img", { name: "图片.png" });
  expect(image).toHaveAttribute("src", "/api/v1/storage/files/image-1/view?workspace_id=w");
  fireEvent.error(image);
  rerender(<FileSpaceReader fileId="image-2" name="另一张.jpg" workspaceId="w" />);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByRole("img", { name: "另一张.jpg" })).toBeInTheDocument();
});
