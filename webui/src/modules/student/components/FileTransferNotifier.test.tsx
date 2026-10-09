import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, vi } from "vitest";

import { api } from "@/platform/http/api";
import { FileTransferNotifier } from "./FileTransferNotifier";

vi.mock("@/platform/http/api", () => ({
  api: { getFileTransferSummary: vi.fn() },
}));

beforeEach(() => {
  vi.mocked(api.getFileTransferSummary).mockReset();
});

it("shows one aggregated login reminder for simultaneous transfer requests", async () => {
  vi.mocked(api.getFileTransferSummary).mockResolvedValue({ pending_count: 4 });
  const openMessages = vi.fn();

  render(<FileTransferNotifier userId="user-1" onOpenMessages={openMessages} />);

  expect(await screen.findByText("收到 4 个文件请求")).toBeVisible();
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "查看消息" }));
  await waitFor(() => expect(openMessages).toHaveBeenCalledTimes(1));
});

it("reminds again when new requests arrive after the inbox was cleared", async () => {
  vi.mocked(api.getFileTransferSummary)
    .mockResolvedValueOnce({ pending_count: 2 })
    .mockResolvedValueOnce({ pending_count: 0 })
    .mockResolvedValueOnce({ pending_count: 1 });

  render(<FileTransferNotifier userId="user-1" onOpenMessages={vi.fn()} />);

  expect(await screen.findByText("收到 2 个文件请求")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "关闭文件请求提醒" }));

  fireEvent(window, new Event("file-transfers:changed"));
  await waitFor(() => expect(api.getFileTransferSummary).toHaveBeenCalledTimes(2));
  await act(async () => Promise.resolve());
  fireEvent(window, new Event("file-transfers:changed"));

  expect(await screen.findByText("收到 1 个文件请求")).toBeVisible();
});
