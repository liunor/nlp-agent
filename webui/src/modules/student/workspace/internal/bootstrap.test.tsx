import { act, renderHook, waitFor } from "@testing-library/react";
import { useState } from "react";

import { api } from "@/platform/http/api";
import type { AuthSession, RuntimeModelProfile } from "@/shared/types";
import { useWorkspaceBootstrap } from "./bootstrap";
import { useSettingsController } from "./settings-controller";

vi.mock("@/platform/http/api", () => ({
  ApiError: class extends Error {}, ensureAuth: vi.fn(),
  api: { getSettings: vi.fn(), updateSettings: vi.fn() },
}));

const loadSessions = async () => [];
const a: AuthSession = { user_id: "a", csrf_token: "a", workspace_ids: ["default"], roles: ["student"], expires_at: 9999999999 };
const b: AuthSession = { ...a, user_id: "b" };

function useHarness(auth: AuthSession | undefined) {
  const controller = useSettingsController(auth?.user_id ?? null);
  const [profiles, setModelProfiles] = useState<Record<string, RuntimeModelProfile>>({});
  const [, setWorkspaceId] = useState("default");
  const [, setAuthSession] = useState<AuthSession | null>(null);
  const [bootStatus, setBootStatus] = useState<"loading" | "ready" | "unauthenticated" | "error">("loading");
  const [, setError] = useState("");
  useWorkspaceBootstrap({ authRevision: 0, authProviderPresent: true, initialSession: auth, loadSessions,
    initializeSettings: controller.initializeSettings, setModelProfiles, setWorkspaceId, setAuthSession, setBootStatus, setError });
  return { ...controller, profiles, bootStatus };
}

it("does not publish a delayed settings GET after account switch or logout", async () => {
  let finishA!: (value: Awaited<ReturnType<typeof api.getSettings>>) => void;
  vi.mocked(api.getSettings).mockReturnValueOnce(new Promise((resolve) => { finishA = resolve; }))
    .mockResolvedValueOnce({ preferences: { settings: { thinking_level: "deep" } }, runtime: {
      default_model_profile: "deepseek", model_profiles: { deepseek: { label: "B model", provider: "deepseek", available: true, supported_levels: ["deep"] } },
    } });
  const { result, rerender } = renderHook(({ auth }) => useHarness(auth), { initialProps: { auth: a as AuthSession | undefined } });
  await waitFor(() => expect(api.getSettings).toHaveBeenCalledTimes(1));
  const aSignal = vi.mocked(api.getSettings).mock.calls[0][0];
  rerender({ auth: b });
  await waitFor(() => expect(result.current.bootStatus).toBe("ready"));
  await act(async () => finishA({ preferences: { settings: { thinking_level: "extreme" } }, runtime: { default_model_profile: "old", model_profiles: {} } }));
  expect(aSignal?.aborted).toBe(true);
  expect(result.current.settings.thinking_level).toBe("deep");
  expect(result.current.profiles.deepseek.label).toBe("B model");
  rerender({ auth: undefined });
  expect(result.current.settings.thinking_level).toBe("light");
  expect(result.current.profiles).toEqual({});
  expect(result.current.bootStatus).toBe("unauthenticated");
});

it("does not reload settings or erase an in-flight selection on same-account auth refresh", async () => {
  vi.mocked(api.getSettings).mockReset();
  vi.mocked(api.getSettings).mockResolvedValue({ preferences: { settings: { thinking_level: "light" } }, runtime: { default_model_profile: "deepseek", model_profiles: {} } });
  let finish!: (value: { settings: { thinking_level: "deep" } }) => void;
  vi.mocked(api.updateSettings).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  const { result, rerender } = renderHook(({ auth }) => useHarness(auth), { initialProps: { auth: a as AuthSession | undefined } });
  await waitFor(() => expect(result.current.bootStatus).toBe("ready"));
  let saving!: Promise<void>;
  await act(async () => { saving = result.current.patchSettings({ thinking_level: "deep" }); });
  rerender({ auth: { ...a, csrf_token: "rotated-csrf", expires_at: a.expires_at + 100 } });
  expect(api.getSettings).toHaveBeenCalledTimes(1);
  expect(result.current.bootStatus).toBe("ready");
  expect(result.current.settings.thinking_level).toBe("deep");
  expect(result.current.selectionPending).toBe(true);
  await act(async () => { finish({ settings: { thinking_level: "deep" } }); await saving; });
  expect(result.current.selectionPending).toBe(false);
  rerender({ auth: { ...a, roles: ["student", "teacher"] } });
  await waitFor(() => expect(api.getSettings).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(result.current.bootStatus).toBe("ready"));
});
