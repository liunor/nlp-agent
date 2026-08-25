import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/platform/http/api";
import { setAppLanguage } from "@/shared/i18n";
import { normalizeLocale } from "@/shared/i18n/config";
import type { UserSettings } from "@/shared/types";

import { DEFAULT_SETTINGS } from "./constants";

export function useSettingsController() {
  const [settings, setSettings] = useState<UserSettings>(DEFAULT_SETTINGS);
  const confirmedSettingsRef = useRef(settings);
  const pendingSettingsPatches = useRef<Array<{ id: number; patch: Partial<UserSettings> }>>([]);
  const nextSettingsMutation = useRef(0);
  const settingsSaveQueue = useRef<Promise<void>>(Promise.resolve());
  const [settingsError, setSettingsError] = useState("");
  const settingsErrorMutation = useRef(0);

  const initializeSettings = useCallback((loadedSettings: UserSettings) => {
    confirmedSettingsRef.current = loadedSettings;
    setSettings(loadedSettings);
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    void setAppLanguage(normalizeLocale(settings.locale));
    root.dataset.contentFontSize = settings.content_font_size;
    root.dataset.reduceMotion = String(settings.reduce_motion);
    const media = matchMedia("(prefers-color-scheme: dark)");
    const applyTheme = () => {
      const dark = settings.theme === "dark" || (settings.theme === "system" && media.matches);
      root.classList.toggle("dark", dark);
    };
    applyTheme();
    if (settings.theme !== "system") return;
    media.addEventListener("change", applyTheme);
    return () => media.removeEventListener("change", applyTheme);
  }, [settings.content_font_size, settings.locale, settings.reduce_motion, settings.theme]);

  const patchSettings = useCallback(async (patch: Partial<UserSettings>) => {
    const mutation = ++nextSettingsMutation.current;
    pendingSettingsPatches.current.push({ id: mutation, patch });
    const optimistic = pendingSettingsPatches.current.reduce(
      (current, item) => ({ ...current, ...item.patch }),
      confirmedSettingsRef.current,
    );
    setSettings(optimistic);
    setSettingsError("");
    try {
      const request = settingsSaveQueue.current.then(() => api.updateSettings(patch));
      settingsSaveQueue.current = request.then(() => undefined, () => undefined);
      const response = await request;
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
      pendingSettingsPatches.current = pendingSettingsPatches.current.filter((item) => item.id !== mutation);
      setSettings(pendingSettingsPatches.current.reduce(
        (current, item) => ({ ...current, ...item.patch }),
        confirmedSettingsRef.current,
      ));
      settingsErrorMutation.current = mutation;
      setSettingsError(`设置保存失败：${reason instanceof Error ? reason.message : String(reason)}`);
    }
  }, []);
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

  return { settings, settingsError, initializeSettings, patchSettings, resetSettings };
}
