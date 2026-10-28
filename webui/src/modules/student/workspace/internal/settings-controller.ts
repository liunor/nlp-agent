import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { api } from "@/platform/http/api";
import { setAppLanguage } from "@/shared/i18n";
import { normalizeLocale } from "@/shared/i18n/config";
import type { UserSettings } from "@/shared/types";

import { DEFAULT_SETTINGS } from "./constants";

export function useSettingsController(accountKey: string | null) {
  const accountToken = useMemo(() => ({ owner: accountKey }), [accountKey]);
  const [settings, setSettings] = useState<UserSettings>(DEFAULT_SETTINGS);
  const [settingsOwner, setSettingsOwner] = useState(accountKey);
  const scope = useRef({ owner: accountKey, token: accountToken, generation: 0, initialized: false, expectedUserId: undefined as string | undefined });
  const inFlight = useRef(new Set<AbortController>());
  const confirmedSettingsRef = useRef(settings);
  const pendingSettingsPatches = useRef<Array<{ id: number; patch: Partial<UserSettings> }>>([]);
  const nextSettingsMutation = useRef(0);
  const settingsSaveQueue = useRef<Promise<void>>(Promise.resolve());
  const [settingsError, setSettingsError] = useState("");
  const settingsErrorMutation = useRef(0);
  const [selectionPending, setSelectionPending] = useState(false);
  const [selectionSaving, setSelectionSaving] = useState(false);
  const previewActive = useRef(false);
  const selectionBlocked = useRef(false);
  const refreshSelectionPending = useCallback(() => {
    const saving = pendingSettingsPatches.current.some(({ patch }) =>
      patch.thinking_level !== undefined || patch.model_profile !== undefined);
    selectionBlocked.current = previewActive.current || saving;
    setSelectionSaving(saving);
    setSelectionPending(selectionBlocked.current);
  }, []);

  const invalidateScope = useCallback(() => {
    scope.current.generation += 1;
    scope.current.initialized = false;
    inFlight.current.forEach((controller) => controller.abort());
    inFlight.current.clear();
    pendingSettingsPatches.current = [];
    settingsSaveQueue.current = Promise.resolve();
    confirmedSettingsRef.current = DEFAULT_SETTINGS;
    settingsErrorMutation.current = 0;
    previewActive.current = false;
    selectionBlocked.current = false;
  }, []);

  const clearSettings = useCallback(() => {
    invalidateScope();
    setSettings(DEFAULT_SETTINGS);
    setSettingsOwner(scope.current.owner);
    setSettingsError("");
    setSelectionPending(false);
    setSelectionSaving(false);
  }, [invalidateScope]);

  // Reset owned state during render so even the first render of a new account
  // cannot expose the previous account's settings (including an A→B→A switch).
  if (settingsOwner !== accountKey) {
    setSettingsOwner(accountKey);
    setSettings(DEFAULT_SETTINGS);
    setSettingsError("");
    setSelectionPending(false);
    setSelectionSaving(false);
  }

  useLayoutEffect(() => {
    scope.current.owner = accountKey;
    scope.current.token = accountToken;
    scope.current.expectedUserId = accountKey?.startsWith("embedded:") ? undefined : accountKey ?? undefined;
    invalidateScope();
    const requests = inFlight.current;
    const currentScope = scope.current;
    return () => {
      currentScope.generation += 1;
      currentScope.initialized = false;
      requests.forEach((controller) => controller.abort());
      requests.clear();
    };
  }, [accountKey, accountToken, invalidateScope]);

  const initializeSettings = useCallback((loadedSettings: UserSettings, expectedUserId?: string) => {
    scope.current.initialized = scope.current.owner !== null;
    if (expectedUserId) scope.current.expectedUserId = expectedUserId;
    confirmedSettingsRef.current = loadedSettings;
    setSettingsOwner(scope.current.owner);
    setSettings(loadedSettings);
    setSettingsError("");
  }, []);

  const visibleSettings = settingsOwner === accountKey ? settings : DEFAULT_SETTINGS;

  useEffect(() => {
    const root = document.documentElement;
    void setAppLanguage(normalizeLocale(visibleSettings.locale));
    root.dataset.contentFontSize = visibleSettings.content_font_size;
    root.dataset.reduceMotion = String(visibleSettings.reduce_motion);
    const media = matchMedia("(prefers-color-scheme: dark)");
    const applyTheme = () => {
      const dark = visibleSettings.theme === "dark" || (visibleSettings.theme === "system" && media.matches);
      root.classList.toggle("dark", dark);
    };
    applyTheme();
    if (visibleSettings.theme !== "system") return;
    media.addEventListener("change", applyTheme);
    return () => media.removeEventListener("change", applyTheme);
  }, [visibleSettings.content_font_size, visibleSettings.locale, visibleSettings.reduce_motion, visibleSettings.theme]);

  const patchSettings = useCallback(async (patch: Partial<UserSettings>) => {
    const generation = scope.current.generation;
    const isCurrent = () => accountKey !== null && scope.current.token === accountToken && scope.current.generation === generation && scope.current.initialized;
    if (!isCurrent()) return;
    const mutation = ++nextSettingsMutation.current;
    pendingSettingsPatches.current.push({ id: mutation, patch });
    refreshSelectionPending();
    const optimistic = pendingSettingsPatches.current.reduce(
      (current, item) => ({ ...current, ...item.patch }),
      confirmedSettingsRef.current,
    );
    setSettings(optimistic);
    setSettingsError("");
    try {
      const request = settingsSaveQueue.current.then(async () => {
        if (!isCurrent()) return;
        const controller = new AbortController();
        inFlight.current.add(controller);
        try { return await api.updateSettings(patch, controller.signal, scope.current.expectedUserId); }
        finally { inFlight.current.delete(controller); }
      });
      settingsSaveQueue.current = request.then(() => undefined, () => undefined);
      const response = await request;
      if (!isCurrent() || !response) return;
      confirmedSettingsRef.current = {
        ...confirmedSettingsRef.current,
        ...patch,
        ...response.settings,
      };
      pendingSettingsPatches.current = pendingSettingsPatches.current.filter((item) => item.id !== mutation);
      setSettings(pendingSettingsPatches.current.reduce(
        (current, item) => ({ ...current, ...item.patch }),
        confirmedSettingsRef.current,
      ));
      if (settingsErrorMutation.current <= mutation) {
        settingsErrorMutation.current = 0;
        setSettingsError("");
      }
    } catch (reason) {
      if (!isCurrent()) return;
      pendingSettingsPatches.current = pendingSettingsPatches.current.filter((item) => item.id !== mutation);
      setSettings(pendingSettingsPatches.current.reduce(
        (current, item) => ({ ...current, ...item.patch }),
        confirmedSettingsRef.current,
      ));
      settingsErrorMutation.current = mutation;
      setSettingsError(`设置保存失败：${reason instanceof Error ? reason.message : String(reason)}`);
    } finally {
      if (isCurrent()) {
        pendingSettingsPatches.current = pendingSettingsPatches.current.filter((item) => item.id !== mutation);
        refreshSelectionPending();
      }
    }
  }, [accountKey, accountToken, refreshSelectionPending]);
  const setSelectionPreview = useCallback((active: boolean) => {
    if (scope.current.token !== accountToken || !scope.current.initialized) return;
    previewActive.current = active;
    refreshSelectionPending();
  }, [accountToken, refreshSelectionPending]);
  const isSelectionReady = useCallback((expected: UserSettings) => accountKey !== null && scope.current.token === accountToken
    && scope.current.initialized && !selectionBlocked.current
    && expected.thinking_level === confirmedSettingsRef.current.thinking_level
    && expected.model_profile === confirmedSettingsRef.current.model_profile, [accountKey, accountToken]);
  const resetSettings = useCallback(() => {
    void patchSettings({
      locale: DEFAULT_SETTINGS.locale,
      theme: DEFAULT_SETTINGS.theme,
      content_font_size: DEFAULT_SETTINGS.content_font_size,
      reduce_motion: DEFAULT_SETTINGS.reduce_motion,
      show_reasoning: DEFAULT_SETTINGS.show_reasoning,
      stream_render_interval_ms: DEFAULT_SETTINGS.stream_render_interval_ms,
    });
  }, [patchSettings]);

  return { settings: visibleSettings, settingsError: settingsOwner === accountKey ? settingsError : "", selectionPending: settingsOwner === accountKey && selectionPending, selectionSaving: settingsOwner === accountKey && selectionSaving, setSelectionPreview, isSelectionReady, initializeSettings, patchSettings, resetSettings, clearSettings };
}
