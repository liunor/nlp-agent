import { act, renderHook } from "@testing-library/react";

import { api } from "@/platform/http/api";
import type { UserSettings } from "@/shared/types";
import { DEFAULT_SETTINGS } from "./constants";
import { useSettingsController } from "./settings-controller";

vi.mock("@/platform/http/api", () => ({ api: { updateSettings: vi.fn() } }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

beforeEach(() => { vi.mocked(api.updateSettings).mockReset(); });

it("drops an old account's queued writes and late success after switching accounts", async () => {
  const pending = deferred<{ settings: { thinking_level: "extreme" } }>();
  vi.mocked(api.updateSettings).mockReturnValueOnce(pending.promise);
  const { result, rerender } = renderHook(({ owner }) => useSettingsController(owner), { initialProps: { owner: "a" as string | null } });
  act(() => result.current.initializeSettings({ ...DEFAULT_SETTINGS, thinking_level: "quick" }));
  let first!: Promise<void>;
  let queued!: Promise<void>;
  await act(async () => {
    first = result.current.patchSettings({ thinking_level: "extreme" });
    queued = result.current.patchSettings({ theme: "dark" });
    await Promise.resolve();
  });
  rerender({ owner: "b" });
  expect(result.current.settings.thinking_level).toBe("light");
  act(() => result.current.initializeSettings({ ...DEFAULT_SETTINGS, thinking_level: "deep" }));
  await act(async () => { pending.resolve({ settings: { thinking_level: "extreme" } }); await Promise.all([first, queued]); });
  expect(api.updateSettings).toHaveBeenCalledTimes(1);
  expect(result.current.settings.thinking_level).toBe("deep");
  expect(result.current.settings.theme).toBe("light");
  expect(result.current.settingsError).toBe("");
});

it("ignores an old account's late failure and aborts its in-flight request", async () => {
  const pending = deferred<{ settings: Partial<UserSettings> }>();
  vi.mocked(api.updateSettings).mockReturnValueOnce(pending.promise);
  const { result, rerender } = renderHook(({ owner }) => useSettingsController(owner), { initialProps: { owner: "a" } });
  act(() => result.current.initializeSettings({ ...DEFAULT_SETTINGS, thinking_level: "quick" }));
  let saving!: Promise<void>;
  await act(async () => { saving = result.current.patchSettings({ thinking_level: "extreme" }); await Promise.resolve(); });
  const signal = vi.mocked(api.updateSettings).mock.calls[0][1];
  rerender({ owner: "b" });
  act(() => result.current.initializeSettings({ ...DEFAULT_SETTINGS, thinking_level: "deep" }));
  await act(async () => { pending.reject(new Error("A failed")); await saving; });
  expect(signal?.aborted).toBe(true);
  expect(result.current.settings.thinking_level).toBe("deep");
  expect(result.current.settingsError).toBe("");
});

it("clears settings on logout and prevents retained account callbacks from writing", async () => {
  const { result, rerender } = renderHook(({ owner }) => useSettingsController(owner), { initialProps: { owner: "a" as string | null } });
  act(() => result.current.initializeSettings({ ...DEFAULT_SETTINGS, thinking_level: "extreme" }));
  const oldSave = result.current.patchSettings;
  rerender({ owner: null });
  expect(result.current.settings.thinking_level).toBe("light");
  await act(async () => { await oldSave({ thinking_level: "deep" }); await result.current.patchSettings({ thinking_level: "quick" }); });
  expect(api.updateSettings).not.toHaveBeenCalled();
  expect(result.current.settings.thinking_level).toBe("light");
});

it("ignores pending writes after unmount", async () => {
  const pending = deferred<{ settings: Partial<UserSettings> }>();
  vi.mocked(api.updateSettings).mockReturnValueOnce(pending.promise);
  const { result, unmount } = renderHook(() => useSettingsController("a"));
  act(() => result.current.initializeSettings(DEFAULT_SETTINGS));
  let first!: Promise<void>;
  let second!: Promise<void>;
  await act(async () => {
    first = result.current.patchSettings({ thinking_level: "extreme" });
    second = result.current.patchSettings({ thinking_level: "deep" });
    await Promise.resolve();
  });
  unmount();
  pending.resolve({ settings: {} });
  await Promise.all([first, second]);
  expect(api.updateSettings).toHaveBeenCalledTimes(1);
});

it("does not revive an old save callback after A switches to B and back to A", async () => {
  const { result, rerender } = renderHook(({ owner }) => useSettingsController(owner), { initialProps: { owner: "a" } });
  act(() => result.current.initializeSettings(DEFAULT_SETTINGS));
  const oldSave = result.current.patchSettings;
  rerender({ owner: "b" });
  rerender({ owner: "a" });
  act(() => result.current.initializeSettings({ ...DEFAULT_SETTINGS, thinking_level: "deep" }));
  await act(async () => { await oldSave({ thinking_level: "quick" }); });
  expect(api.updateSettings).not.toHaveBeenCalled();
  expect(result.current.settings.thinking_level).toBe("deep");
});
