import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi, expect, it } from "vitest";
import { api } from "@/platform/http/api";
import { Composer } from "./Composer";

vi.mock("@/platform/http/api", () => ({ uploadAttachment: vi.fn(), api: { uploadStorageFile: vi.fn() } }));

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
