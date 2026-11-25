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
