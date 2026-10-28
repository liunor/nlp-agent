import { useCallback, useEffect, useLayoutEffect, useRef, type Dispatch, type SetStateAction } from "react";

import { ApiError, api, ensureAuth } from "@/platform/http/api";
import type { AuthSession, RuntimeModelProfile, UserSettings } from "@/shared/types";
import { resolveWorkspaceId } from "@/shared/utils/workspace";

import { DEFAULT_SETTINGS } from "./constants";

interface BootstrapOptions {
  authRevision: number;
  authProviderPresent?: boolean;
  initialSession?: AuthSession;
  loadSessions: (isCurrent?: () => boolean) => Promise<unknown>;
  initializeSettings: (settings: UserSettings, expectedUserId?: string) => void;
  setModelProfiles: Dispatch<SetStateAction<Record<string, RuntimeModelProfile>>>;
  setWorkspaceId: Dispatch<SetStateAction<string>>;
  setAuthSession: Dispatch<SetStateAction<AuthSession | null>>;
  setBootStatus: Dispatch<SetStateAction<"loading" | "ready" | "unauthenticated" | "error">>;
  setError: Dispatch<SetStateAction<string>>;
}

export function useWorkspaceBootstrap({
  authRevision,
  authProviderPresent = false,
  initialSession,
  loadSessions,
  initializeSettings,
  setModelProfiles,
  setWorkspaceId,
  setAuthSession,
  setBootStatus,
  setError,
}: BootstrapOptions) {
  const activeRequest = useRef<AbortController | null>(null);
  const initialSessionRef = useRef(initialSession);
  useLayoutEffect(() => { initialSessionRef.current = initialSession; }, [initialSession]);
  // A connection-ready auth refresh rotates transport metadata. Reinitializing
  // for that new object would reopen the socket and trigger another refresh.
  const sessionIdentity = JSON.stringify(initialSession ? [
    initialSession.user_id,
    [...(initialSession.workspace_ids ?? [])].sort(),
    [...(initialSession.roles ?? [])].sort(),
    [...(initialSession.permissions ?? [])].sort(),
  ] : null);
  const cancelBootstrap = useCallback(() => { activeRequest.current?.abort(); }, []);
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    activeRequest.current = controller;
    setBootStatus("loading");
    setModelProfiles({});
    void (async () => {
      try {
        const currentSession = initialSessionRef.current;
        if (authProviderPresent && !currentSession) {
          initializeSettings(DEFAULT_SETTINGS);
          setAuthSession(null);
          setBootStatus("unauthenticated");
          return;
        }
        const auth = authProviderPresent ? currentSession! : await ensureAuth();
        if (cancelled || controller.signal.aborted) return;
        const [, settingsResponse] = await Promise.all([loadSessions(() => !cancelled && !controller.signal.aborted), api.getSettings(controller.signal, auth.user_id)]);
        if (cancelled || controller.signal.aborted) return;
        const loadedSettings = {
          ...DEFAULT_SETTINGS,
          model_profile: settingsResponse.runtime.default_model_profile,
          ...(settingsResponse.preferences.settings ?? {}),
        };
        initializeSettings(loadedSettings, auth.user_id);
        setModelProfiles(settingsResponse.runtime.model_profiles);
        setWorkspaceId(resolveWorkspaceId(auth, loadedSettings));
        setAuthSession(auth);
        setBootStatus("ready");
      } catch (reason) {
        if (cancelled || controller.signal.aborted) return;
        if (reason instanceof ApiError && reason.status === 401) {
          setBootStatus("unauthenticated");
          return;
        }
        setError(reason instanceof Error ? reason.message : String(reason));
        setBootStatus("error");
      }
    })();
    return () => { cancelled = true; controller.abort(); };
  }, [authProviderPresent, authRevision, sessionIdentity, initializeSettings, loadSessions, setAuthSession, setBootStatus, setError, setModelProfiles, setWorkspaceId]);
  return cancelBootstrap;
}
