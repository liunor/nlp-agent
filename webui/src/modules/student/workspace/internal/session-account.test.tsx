import { act, renderHook } from "@testing-library/react";

import { api } from "@/platform/http/api";
import { loadLearningPreferences } from "@/platform/storage/learning-preferences";
import type { SessionListResponse } from "@/shared/types";
import { useSessionController } from "./session-controller";

vi.mock("@/platform/http/api", () => ({ api: { listSessions: vi.fn(), deleteSession: vi.fn(), renameSession: vi.fn() } }));

it("drops a session refresh started by the previous account", async () => {
  let finishA!: (value: SessionListResponse) => void;
  vi.mocked(api.listSessions).mockReturnValueOnce(new Promise((resolve) => { finishA = resolve; }))
    .mockResolvedValueOnce({ items: [] });
  const shared = { preferences: loadLearningPreferences(), persistPreferences: vi.fn(), updateSessionMeta: vi.fn() };
  const { result, rerender } = renderHook(({ accountKey }) => useSessionController({ ...shared, accountKey }), { initialProps: { accountKey: "a" } });
  let oldRequest!: Promise<unknown>;
  act(() => { oldRequest = result.current.loadSessions(); });
  rerender({ accountKey: "b" });
  await act(async () => { await result.current.loadSessions(); });
  await act(async () => { finishA({ items: [{ session_id: "a-private", user_id: "a", workspace_id: "default", channel: "web" }] }); await oldRequest; });
  expect(result.current.sessions).toEqual([]);
});

it("does not let an old account's delete response remove the new account's sessions", async () => {
  let finishDelete!: () => void;
  vi.mocked(api.deleteSession).mockReturnValueOnce(new Promise((resolve) => { finishDelete = () => resolve(undefined); }));
  const shared = { preferences: loadLearningPreferences(), persistPreferences: vi.fn(), updateSessionMeta: vi.fn() };
  const { result, rerender } = renderHook(({ accountKey }) => useSessionController({ ...shared, accountKey }), { initialProps: { accountKey: "a" } });
  act(() => { result.current.setSessions([{ session_id: "old", user_id: "a", workspace_id: "default", channel: "web" }]); });
  let oldDelete!: Promise<void>;
  act(() => { oldDelete = result.current.deleteSession("old"); });
  rerender({ accountKey: "b" });
  act(() => { result.current.setSessions([{ session_id: "new", user_id: "b", workspace_id: "default", channel: "web" }]); });
  await act(async () => { finishDelete(); await oldDelete; });
  expect(result.current.sessions.map((session) => session.session_id)).toEqual(["new"]);
  expect(shared.persistPreferences).not.toHaveBeenCalled();
});

it("does not surface a previous account's delayed rename error", async () => {
  let failRename!: (reason: Error) => void;
  vi.mocked(api.renameSession).mockReturnValueOnce(new Promise((_resolve, reject) => { failRename = reject; }));
  const onRequestError = vi.fn();
  const shared = { preferences: loadLearningPreferences(), persistPreferences: vi.fn(), updateSessionMeta: vi.fn(), onRequestError };
  const { result, rerender } = renderHook(({ accountKey }) => useSessionController({ ...shared, accountKey }), { initialProps: { accountKey: "a" } });
  let oldRename!: Promise<void>;
  act(() => { oldRename = result.current.renameSessionTitle("old", "old title"); });
  rerender({ accountKey: "b" });
  await act(async () => { failRename(new Error("old request failed")); await oldRename; });
  expect(onRequestError).not.toHaveBeenCalled();
});
