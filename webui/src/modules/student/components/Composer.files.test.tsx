import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi, expect, it } from "vitest";
import { api } from "@/platform/http/api";
import { Composer } from "./Composer";

vi.mock("@/platform/http/api", () => ({ uploadAttachment: vi.fn(), api: { uploadStorageFile: vi.fn(), listStorageFiles: vi.fn() } }));

it("uploads multiple documents to file space and sends stable references", async () => {
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:doc"), revokeObjectURL: vi.fn() }));
  vi.mocked(api.uploadStorageFile).mockResolvedValue({ id: "f", kind: "file", name: "note.txt", size_bytes: 3, mime_type: "text/plain", created_at: null, updated_at: null });
  const send = vi.fn();
  const { container } = render(<Composer sessionId="s" workspaceId="w" disabled={false} running={false} onSend={send} onCancel={vi.fn()} />);
  const input = container.querySelector('input[type="file"]')!;
  fireEvent.change(input, { target: { files: [new File(["abc"], "note.txt", { type: "text/plain" })] } });
  await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  expect(send.mock.calls[0][1][0]).toMatchObject({ fileId: "f", displayName: "note.txt", status: "ready" });
  expect(api.uploadStorageFile).toHaveBeenCalledWith(expect.any(File), "w", undefined, expect.any(String), expect.any(AbortSignal));
});


it("uploads a pasted PDF through the document pipeline", async () => {
  vi.mocked(api.uploadStorageFile).mockClear();
  vi.mocked(api.uploadStorageFile).mockResolvedValue({ id: "pdf", kind: "file", name: "paper.pdf", size_bytes: 3, mime_type: "application/pdf", created_at: null, updated_at: null });
  render(<Composer sessionId="s" workspaceId="w" disabled={false} running={false} onSend={vi.fn()} onCancel={vi.fn()} />);
  const file = new File(["pdf"], "paper.pdf", { type: "application/pdf" });
  fireEvent.paste(screen.getByRole("textbox"), { clipboardData: { items: [{ kind: "file", type: file.type, getAsFile: () => file }], files: [file] } });
  await waitFor(() => expect(api.uploadStorageFile).toHaveBeenCalledWith(file, "w", undefined, expect.any(String), expect.any(AbortSignal)));
});


it("keeps file-space actions behind the plus menu and opens a picker dialog", async () => {
  vi.mocked(api.listStorageFiles).mockResolvedValue({ items: [] });
  render(<Composer sessionId="s" workspaceId="w" disabled={false} running={false} onSend={vi.fn()} onCancel={vi.fn()} />);
  expect(screen.queryByText("文件空间")).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.keyDown(screen.getByRole("button", { name: "上传附件" }), { key: "ArrowDown" });
  const choice = await screen.findByRole("menuitem", { name: "从文件空间选择" });
  expect(screen.getByRole("menuitem", { name: "上传文件" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "上传附件" })).toBeVisible();
  expect(document.body).not.toHaveAttribute("data-scroll-locked");
  expect(screen.getByRole("menu")).toHaveAttribute("data-side", "bottom");
  fireEvent.click(choice);
  expect(await screen.findByRole("dialog", { name: "从文件空间添加附件" })).toBeInTheDocument();
  expect(api.listStorageFiles).toHaveBeenCalledWith("w", undefined);
  fireEvent.click(screen.getByRole("button", { name: "关闭文件选择" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});
