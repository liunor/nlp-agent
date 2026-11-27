import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, vi } from "vitest";

import { api } from "@/platform/http/api";
import { FileTransferNotifier } from "./FileTransferNotifier";

vi.mock("@/platform/http/api", () => ({
  api: {
    getFileTransferSummary: vi.fn(),
    getFileTransferUnreadSummary: vi.fn(),
    markFileTransfersSeen: vi.fn(),
  },
}));

beforeEach(() => {
  vi.mocked(api.getFileTransferSummary).mockReset();
  vi.mocked(api.getFileTransferUnreadSummary).mockReset();
  vi.mocked(api.markFileTransfersSeen).mockReset();
});

it("shows one non-blocking aggregated toast for simultaneous transfer requests", async () => {
  vi.mocked(api.getFileTransferUnreadSummary).mockResolvedValue({
    pending_count: 4,
    unseen_accepted_count: 0,
    unread_count: 4,
    notification_version: 4,
  });
  const openMessages = vi.fn();
  const onUnreadCountChange = vi.fn();

  const { container } = render(
    <FileTransferNotifier
      userId="user-1"
      onOpenMessages={openMessages}
      onUnreadCountChange={onUnreadCountChange}
    />,
  );

  expect(await screen.findByText("收到 4 个文件请求")).toBeVisible();
  expect(screen.getAllByRole("status")).toHaveLength(1);
  expect(container.querySelector(".file-transfer-overlay")).toBeNull();
  expect(onUnreadCountChange).toHaveBeenCalledWith(4);
  fireEvent.click(screen.getByRole("button", { name: "查看消息" }));
  await waitFor(() => expect(openMessages).toHaveBeenCalledTimes(1));
});

it("reminds again when new requests arrive after the inbox was cleared", async () => {
  vi.mocked(api.getFileTransferUnreadSummary)
    .mockResolvedValueOnce({ pending_count: 2, unseen_accepted_count: 0, unread_count: 2, notification_version: 2 })
    .mockResolvedValueOnce({ pending_count: 0, unseen_accepted_count: 0, unread_count: 0, notification_version: 2 })
    .mockResolvedValueOnce({ pending_count: 1, unseen_accepted_count: 0, unread_count: 1, notification_version: 3 });

  render(<FileTransferNotifier userId="user-1" onOpenMessages={vi.fn()} />);

  expect(await screen.findByText("收到 2 个文件请求")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "关闭文件请求提醒" }));

  fireEvent(window, new Event("file-transfers:changed"));
  await waitFor(() => expect(api.getFileTransferUnreadSummary).toHaveBeenCalledTimes(2));
  await act(async () => Promise.resolve());
  fireEvent(window, new Event("file-transfers:changed"));

  expect(await screen.findByText("收到 1 个文件请求")).toBeVisible();
});

it("reminds when a new request replaces a handled request without changing the count", async () => {
  vi.mocked(api.getFileTransferUnreadSummary)
    .mockResolvedValueOnce({ pending_count: 1, unseen_accepted_count: 0, unread_count: 1, notification_version: 10 })
    .mockResolvedValueOnce({ pending_count: 1, unseen_accepted_count: 0, unread_count: 1, notification_version: 11 });

  render(<FileTransferNotifier userId="user-1" onOpenMessages={vi.fn()} />);

  expect(await screen.findByText("收到 1 个文件请求")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "关闭文件请求提醒" }));
  fireEvent(window, new Event("file-transfers:changed"));

  expect(await screen.findByText("收到 1 个文件请求")).toBeVisible();
});

it("notifies when directly delivered files arrive, refreshes storage files, and opens my files", async () => {
  vi.mocked(api.getFileTransferUnreadSummary)
    .mockResolvedValueOnce({ pending_count: 0, unseen_accepted_count: 0, unread_count: 0, notification_version: 1 })
    .mockResolvedValueOnce({ pending_count: 0, unseen_accepted_count: 2, unread_count: 2, notification_version: 2 });
  vi.mocked(api.markFileTransfersSeen).mockResolvedValue({ ok: true, updated: 2 });
  const filesChangedSpy = vi.fn();
  window.addEventListener("sandbox:files-changed", filesChangedSpy);
  const onOpenFiles = vi.fn();

  try {
    render(
      <FileTransferNotifier
        userId="user-1"
        onOpenMessages={vi.fn()}
        onOpenFiles={onOpenFiles}
      />,
    );

    await waitFor(() => expect(api.getFileTransferUnreadSummary).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    fireEvent(window, new Event("file-transfers:changed"));
    expect(await screen.findByText("收到 2 个新文件")).toBeVisible();
    expect(filesChangedSpy).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "查看我的文件" }));
    await waitFor(() => expect(api.markFileTransfersSeen).toHaveBeenCalledTimes(1));
    expect(onOpenFiles).toHaveBeenCalledTimes(1);
  } finally {
    window.removeEventListener("sandbox:files-changed", filesChangedSpy);
  }
});
