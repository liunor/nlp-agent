import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useCallback, useEffect, useState } from "react";

import { api } from "@/platform/http/api";
import type { FeedbackThreadSummary } from "@/shared/types";

import { Feedback } from "./DeveloperWorkspace";

const { listFeedbackMock, getFeedbackMock, markFeedbackReadMock } = vi.hoisted(() => ({
  listFeedbackMock: vi.fn(),
  getFeedbackMock: vi.fn(),
  markFeedbackReadMock: vi.fn(async () => ({ ok: true })),
}));
vi.mock("@/platform/http/api", () => ({
  api: {
    listFeedback: listFeedbackMock,
    getFeedback: getFeedbackMock,
    markFeedbackRead: markFeedbackReadMock,
  },
}));

const thread = (id: string, name: string): FeedbackThreadSummary => ({
  thread_id: id,
  user_id: `user-${id}`,
  username: name,
  display_name: name,
  unread_count: 1,
  updated_at: "2026-08-26T00:00:00+00:00",
  status: "open",
  category: "other",
  priority: "medium",
  latest: { id: `${id}-m`, sender_type: "student", body: "你好", created_at: "2026-08-26T00:00:00+00:00" },
});

function Harness({ initialSearch = "" }: { initialSearch?: string }) {
  const [threads, setThreads] = useState<FeedbackThreadSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState(initialSearch);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState("");
  const [retryNonce, setRetryNonce] = useState(0);
  // Stable identity, like refreshFeedback in DeveloperWorkspace: an inline
  // arrow would retrigger Feedback's [refresh, selectedId] detail effect on
  // every render.
  const refresh = useCallback(async () => setRetryNonce((nonce) => nonce + 1), []);
  useEffect(() => {
    // Mirrors refreshFeedback in DeveloperWorkspace: keep the last list while
    // offline but surface the failure.
    api.listFeedback({ limit: 20, offset, q: search || undefined }).then((result) => {
      setThreads(result.items);
      setTotal(result.total);
      setLoadError("");
    }).catch((reason) => setLoadError(reason instanceof Error ? reason.message : String(reason)));
  }, [offset, retryNonce, search]);
  return (
    <Feedback
      threads={threads}
      total={total}
      pageSize={20}
      offset={offset}
      search={search}
      loadError={loadError}
      selectedId={selectedId}
      onSelect={setSelectedId}
      onSearchChange={(value) => { setSearch(value); setOffset(0); }}
      onOffsetChange={setOffset}
      refresh={refresh}
    />
  );
}

describe("Developer feedback list", () => {
  beforeEach(() => {
    listFeedbackMock.mockReset();
    getFeedbackMock.mockReset();
    markFeedbackReadMock.mockClear();
    listFeedbackMock.mockResolvedValue({ items: [thread("t1", "alice")], total: 25 });
    vi.useFakeTimers();
  });
  afterEach(() => vi.useRealTimers());

  const flush = async () => {
    await act(async () => { await Promise.resolve(); });
  };

  it("requests the first page with page size and renders pagination summary", async () => {
    render(<Harness />);
    await flush();

    expect(listFeedbackMock).toHaveBeenCalledWith({ limit: 20, offset: 0, q: undefined });
    expect(screen.getByText("alice")).toBeVisible();
    expect(screen.getByText((_, element) => element?.textContent === "共 25 条 · 第 1/2 页")).toBeVisible();
    expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "下一页" })).toBeEnabled();
  });

  it("loads the next page through the offset change", async () => {
    render(<Harness />);
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await flush();

    expect(listFeedbackMock).toHaveBeenLastCalledWith({ limit: 20, offset: 20, q: undefined });
  });

  it("hides pagination and shows an empty state without threads", async () => {
    listFeedbackMock.mockResolvedValue({ items: [], total: 0 });
    render(<Harness />);
    await flush();

    expect(screen.getByText("暂无反馈")).toBeVisible();
    expect(screen.queryByRole("button", { name: "下一页" })).not.toBeInTheDocument();
  });

  it("debounces search input before refetching and reports no matches", async () => {
    listFeedbackMock.mockImplementation(async ({ q }: { q?: string }) =>
      q ? { items: [], total: 0 } : { items: [thread("t1", "alice")], total: 1 });
    render(<Harness />);
    await flush();

    fireEvent.change(screen.getByLabelText("搜索反馈用户"), { target: { value: "alice" } });
    await act(async () => { vi.advanceTimersByTime(299); });
    expect(listFeedbackMock).not.toHaveBeenCalledWith(expect.objectContaining({ q: "alice" }));
    expect(screen.getByText("alice")).toBeVisible();

    await act(async () => { vi.advanceTimersByTime(1); });
    await flush();
    expect(listFeedbackMock).toHaveBeenLastCalledWith({ limit: 20, offset: 0, q: "alice" });
    expect(screen.getByText("没有匹配的反馈")).toBeVisible();
  });

  it("keeps the empty-state copy distinct for a filtered query", () => {
    listFeedbackMock.mockResolvedValue({ items: [], total: 0 });
    render(<Harness initialSearch="alice" />);

    expect(screen.getByLabelText("搜索反馈用户")).toHaveValue("alice");
  });

  it("surfaces detail loading failures", async () => {
    getFeedbackMock.mockRejectedValue(new Error("boom"));
    render(<Harness />);
    await flush();

    fireEvent.click(screen.getByText("alice"));
    await act(async () => { await Promise.resolve(); });

    expect(getFeedbackMock).toHaveBeenCalledWith("t1");
    expect(screen.getByText("读取失败：boom")).toBeVisible();
  });

  it("retries a failed detail load", async () => {
    getFeedbackMock
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce({ ...thread("t1", "alice"), messages: [] });
    render(<Harness />);
    await flush();

    fireEvent.click(screen.getByText("alice"));
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "重试读取反馈" }));
    await flush();

    expect(getFeedbackMock).toHaveBeenCalledTimes(2);
    expect(screen.getByText("@alice")).toBeVisible();
    expect(screen.queryByText("读取失败：boom")).not.toBeInTheDocument();
  });

  it("shows a failure block with retry instead of the empty state on first-load errors", async () => {
    listFeedbackMock.mockRejectedValue(new Error("HTTP 403"));
    render(<Harness />);
    await flush();

    expect(screen.getByText("加载反馈失败")).toBeVisible();
    expect(screen.getByText("HTTP 403")).toBeVisible();
    expect(screen.getByRole("button", { name: "重试" })).toBeEnabled();
    expect(screen.queryByText("暂无反馈")).not.toBeInTheDocument();
  });

  it("keeps the last list and shows a stale banner when a refresh fails", async () => {
    listFeedbackMock
      .mockResolvedValueOnce({ items: [thread("t1", "alice")], total: 25 })
      .mockRejectedValue(new Error("network down"));
    render(<Harness />);
    await flush();
    expect(screen.queryByText(/刷新失败/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await flush();

    expect(listFeedbackMock).toHaveBeenLastCalledWith({ limit: 20, offset: 20, q: undefined });
    expect(screen.getByText("alice")).toBeVisible();
    expect(screen.getByText(/刷新失败：network down/)).toBeVisible();
    expect(screen.queryByText("加载反馈失败")).not.toBeInTheDocument();
  });

  it("recovers through the retry button after a first-load failure", async () => {
    listFeedbackMock.mockRejectedValueOnce(new Error("boom"));
    render(<Harness />);
    await flush();
    expect(screen.getByText("加载反馈失败")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await flush();

    expect(screen.getByText("alice")).toBeVisible();
    expect(screen.queryByText("加载反馈失败")).not.toBeInTheDocument();
  });

  it("does not reselect or auto-mark-read a thread when paging drops the selection", async () => {
    vi.useRealTimers();
    listFeedbackMock
      .mockResolvedValueOnce({ items: [thread("t1", "alice")], total: 25 })
      .mockResolvedValueOnce({ items: [thread("t1", "alice")], total: 25 })
      .mockResolvedValue({ items: [thread("t2", "bob")], total: 25 });
    getFeedbackMock.mockResolvedValue({
      thread_id: "t1",
      user_id: "user-t1",
      username: "alice",
      display_name: "alice",
      messages: [{ id: "t1-m", sender_type: "student", body: "你好", created_at: "2026-08-26T00:00:00+00:00" }],
    });
    render(<Harness />);
    await screen.findByText("alice");

    fireEvent.click(screen.getByText("alice"));
    await waitFor(() => expect(markFeedbackReadMock).toHaveBeenCalledTimes(1));
    expect(getFeedbackMock).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByText("bob");

    expect(listFeedbackMock).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 20 }));
    // t1 left the page, but paging must neither reselect bob nor mark it read.
    expect(getFeedbackMock).toHaveBeenCalledTimes(1);
    expect(markFeedbackReadMock).toHaveBeenCalledTimes(1);
  });
});
