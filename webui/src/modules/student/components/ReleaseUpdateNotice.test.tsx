import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "@/platform/http/api";
import { ReleaseUpdateNotice } from "./ReleaseUpdateNotice";

vi.mock("@/platform/http/api", () => ({ api: {
  getUnreadReleaseNote: vi.fn(), markReleaseNoteRead: vi.fn(),
} }));
const note = { id: "release-2", version: "2.2.0", released_at: "2026-11-17T00:00:00Z", status: "published" as const, notes: ["文件可以读写啦！", "常用依赖已就位"] };
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getUnreadReleaseNote).mockResolvedValue({ item: note });
  vi.mocked(api.markReleaseNoteRead).mockResolvedValue({ ok: true });
});

it("shows the published content and records a receipt only once when visible", async () => {
  const { rerender } = render(<ReleaseUpdateNotice userId="alice" paused />);
  await waitFor(() => expect(api.getUnreadReleaseNote).toHaveBeenCalledOnce());
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(api.markReleaseNoteRead).not.toHaveBeenCalled();
  rerender(<ReleaseUpdateNotice userId="alice" />);
  expect(await screen.findByRole("dialog", { name: "本次更新与修复" })).toBeVisible();
  expect(screen.getByText("文件可以读写啦！")).toBeVisible();
  expect(screen.getByText("v2.2.0")).toBeVisible();
  await waitFor(() => expect(api.markReleaseNoteRead).toHaveBeenCalledExactlyOnceWith("release-2"));
  fireEvent.click(screen.getByRole("button", { name: "知道啦" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  rerender(<ReleaseUpdateNotice userId="alice" />);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("does not fetch for anonymous visitors or show already read notes", async () => {
  const { rerender } = render(<ReleaseUpdateNotice />);
  expect(api.getUnreadReleaseNote).not.toHaveBeenCalled();
  vi.mocked(api.getUnreadReleaseNote).mockResolvedValue({ item: null });
  rerender(<ReleaseUpdateNotice userId="alice" />);
  await waitFor(() => expect(api.getUnreadReleaseNote).toHaveBeenCalledOnce());
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(api.markReleaseNoteRead).not.toHaveBeenCalled();
});

it("closes with Escape", async () => {
  render(<ReleaseUpdateNotice userId="alice" />);
  fireEvent.keyDown(await screen.findByRole("dialog"), { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("closes when clicking the backdrop", async () => {
  render(<ReleaseUpdateNotice userId="alice" />);
  await screen.findByRole("dialog");
  fireEvent.pointerDown(document.querySelector(".release-update-overlay")!, { button: 0, pointerType: "mouse" });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("does not display the previous account's announcement while the next account loads", async () => {
  const { rerender } = render(<ReleaseUpdateNotice userId="alice" />);
  await screen.findByRole("dialog");
  vi.mocked(api.getUnreadReleaseNote).mockResolvedValue({ item: null });
  rerender(<ReleaseUpdateNotice userId="bob" />);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  await waitFor(() => expect(api.getUnreadReleaseNote).toHaveBeenCalledTimes(2));
});

it("ignores a stale response after logout", async () => {
  let resolve!: (value: { item: typeof note }) => void;
  vi.mocked(api.getUnreadReleaseNote).mockReturnValue(new Promise((done) => { resolve = done; }));
  const { rerender } = render(<ReleaseUpdateNotice userId="alice" />);
  rerender(<ReleaseUpdateNotice />);
  resolve({ item: note });
  await waitFor(() => expect(api.getUnreadReleaseNote).toHaveBeenCalledOnce());
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(api.markReleaseNoteRead).not.toHaveBeenCalled();
});

it("keeps failures out of the main page", async () => {
  vi.mocked(api.getUnreadReleaseNote).mockRejectedValue(new Error("offline"));
  render(<ReleaseUpdateNotice userId="alice" />);
  await waitFor(() => expect(api.getUnreadReleaseNote).toHaveBeenCalledOnce());
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
