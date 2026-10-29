import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { api } from "@/platform/http/api";
import { useOptionalAuth } from "@/platform/auth/AuthContext";
import { StudentSocket } from "@/platform/realtime/client";
import { clearImportedFiles } from "../../components/importedFiles";
import type { AuthSession, ChatMessage, RealtimeRequestError, RuntimeModelProfile } from "@/shared/types";

import { useWorkspaceBootstrap } from "../internal/bootstrap";
import { usePreferencesController } from "../internal/preferences-controller";
import { createRealtimeEventHandler } from "../internal/realtime-reducer";
import { useSessionController } from "../internal/session-controller";
import { useSettingsController } from "../internal/settings-controller";
import { useTurnSender } from "../internal/send-turn";
import { useTurnHistory } from "../internal/turn-history";
import { clearRejectedDrafts, removeRejectedDraft } from "../internal/rejected-drafts";

export function useStudentWorkspace() {
  const globalAuth = useOptionalAuth();
  const [authRevision, setAuthRevision] = useState(0);
  const refreshAuthSession = globalAuth?.refreshSession;
  const {
    preferences,
    preferencesRef,
    persistPreferences,
    updateSessionMeta,
    setLearningContext,
    addCategory,
    renameCategory,
    deleteCategory,
  } = usePreferencesController();
  const settingsAccount = globalAuth ? (globalAuth.user?.user_id ?? null) : `embedded:${authRevision}`;
  const { settings, settingsError, selectionPending, selectionSaving, setSelectionPreview, isSelectionReady, initializeSettings, patchSettings, resetSettings, clearSettings } = useSettingsController(settingsAccount);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const handleActiveSessionChange = useCallback((sessionId: string | null) => {
    setMessages([]);
    setLoadingMessages(Boolean(sessionId));
  }, []);
  const [requestError, setRequestError] = useState<RealtimeRequestError | null>(null);
  const handleRequestError = useCallback((message: string) => {
    setRequestError({ code: "session_error", message });
  }, []);
  const {
    sessions,
    setSessions,
    workspaceId,
    setWorkspaceId,
    activeSessionId,
    composerRevision,
    setActiveSessionId,
    selectSession,
    activeSessionRef,
    freshSessionIdsRef,
    loadSessions,
    invalidateSessionLoads,
    createBackendSession,
    startNewChat,
    deleteSession,
    renameSessionTitle,
  } = useSessionController({
    accountKey: settingsAccount,
    preferences,
    persistPreferences,
    updateSessionMeta,
    onRequestError: handleRequestError,
    onActiveSessionChange: handleActiveSessionChange,
  });
  const [modelProfiles, setModelProfiles] = useState<Record<string, RuntimeModelProfile>>({});
  const [authSession, setAuthSession] = useState<AuthSession | null>(null);
  const [bootStatus, setBootStatus] = useState<"loading" | "ready" | "unauthenticated" | "error">("loading");
  const [error, setError] = useState("");
  const [socketStatus, setSocketStatus] = useState<"connecting" | "connected" | "reconnecting" | "offline">("connecting");
  const socketRef = useRef<StudentSocket | null>(null);
  const pendingRequests = useRef(new Map<string, string>());
  const pendingDrafts = useRef(new Map<string, { sessionId: string; owner: string | null; message: ChatMessage }>());
  const inFlightTurnIds = useRef(new Set<string>());
  const cancelledTurnIds = useRef(new Set<string>());
  const loadGenerationRef = useRef(0);

  const authenticatedUserId = globalAuth !== null
    ? globalAuth.user?.user_id ?? null
    : authSession?.user_id ?? null;
  const loadTurns = useTurnHistory({
    activeSessionRef,
    socketRef,
    preferencesRef,
    inFlightTurnIds,
    loadGenerationRef,
    draftOwner: authenticatedUserId,
    setMessages,
    setLoadingMessages,
    updateSessionMeta,
  });
  const previousAuthenticatedUserIdRef = useRef<string | null>(null);
  useLayoutEffect(() => {
    const previousUserId = previousAuthenticatedUserIdRef.current;
    if (previousUserId !== null && previousUserId !== authenticatedUserId) {
      invalidateSessionLoads();
      loadGenerationRef.current += 1;
      pendingRequests.current.clear();
      pendingDrafts.current.clear();
      inFlightTurnIds.current.clear();
      cancelledTurnIds.current.clear();
      freshSessionIdsRef.current.clear();
      socketRef.current?.close();
      socketRef.current = null;
      startNewChat();
      setSessions([]);
      setAuthSession(null);
      setRequestError(null);
      setError("");
      setBootStatus("loading");
    }
    previousAuthenticatedUserIdRef.current = authenticatedUserId;
  }, [authenticatedUserId, cancelledTurnIds, freshSessionIdsRef, handleRequestError, inFlightTurnIds, invalidateSessionLoads, pendingRequests, setSessions, startNewChat]);
  const handleEvent = useMemo(
    // The factory stores refs for the socket callback; it does not read them during render.
    // eslint-disable-next-line react-hooks/refs
    () => createRealtimeEventHandler({
      socketRef,
      activeSessionRef,
      pendingRequests,
      pendingDrafts,
      inFlightTurnIds,
      cancelledTurnIds,
      setMessages,
      setActiveSessionId,
      setRequestError,
      persistPreferences,
      updateSessionMeta,
      loadSessions,
      loadTurns,
    }),
    [activeSessionRef, cancelledTurnIds, inFlightTurnIds, loadSessions, loadTurns, persistPreferences, setActiveSessionId, socketRef, updateSessionMeta],
  );

  const cancelBootstrap = useWorkspaceBootstrap({
    authRevision,
    authProviderPresent: globalAuth !== null,
    initialSession: globalAuth?.user ?? undefined,
    loadSessions,
    initializeSettings,
    setModelProfiles,
    setWorkspaceId,
    setAuthSession,
    setBootStatus,
    setError,
  });

  useLayoutEffect(() => {
    loadGenerationRef.current += 1;
    activeSessionRef.current = null;
    pendingRequests.current.clear();
    pendingDrafts.current.clear();
    inFlightTurnIds.current.clear();
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      setSessions([]);
      setActiveSessionId(null);
      setMessages([]);
      setAuthSession(null);
      setRequestError(null);
    });
    return () => { active = false; };
  }, [settingsAccount, activeSessionRef, setActiveSessionId, setSessions]);

  useEffect(() => {
    if (bootStatus !== "ready") return;
    const socket = new StudentSocket(
      handleEvent,
      setSocketStatus,
      undefined,
      async () => {
        await refreshAuthSession?.();
        const sessionId = activeSessionRef.current;
        if (sessionId) await loadTurns(sessionId);
      },
    );
    socketRef.current = socket;
    socket.setSession(activeSessionRef.current);
    socket.connect();

    return () => {
      socket.close();
      socketRef.current = null;
    };
  }, [activeSessionRef, bootStatus, refreshAuthSession, handleEvent, loadTurns]);

  useEffect(() => {
    // AuthProvider is the single global 401 boundary. If a request made by
    // this workspace discovers a revoked/changed session, stop the socket and
    // return the workspace to its unauthenticated recovery state as well.
    if (globalAuth?.isAuthenticated !== false || !authSession?.user_id) return;
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      socketRef.current?.close();
      socketRef.current = null;
      setAuthSession(null);
      setSessions([]);
      setActiveSessionId(null);
      setMessages([]);
      setModelProfiles({});
      setBootStatus("unauthenticated");
    });
    return () => { active = false; };
  }, [authSession?.user_id, globalAuth?.isAuthenticated, setActiveSessionId, setSessions]);

  useEffect(() => {
    loadGenerationRef.current += 1;
    cancelledTurnIds.current.clear();
    const freshSession = activeSessionId ? freshSessionIdsRef.current.has(activeSessionId) : false;
    if (!freshSession) setMessages([]);
    setLoadingMessages(Boolean(activeSessionId && !freshSession));
    socketRef.current?.setSession(activeSessionId);
    queueMicrotask(() => {
      if (activeSessionId && freshSessionIdsRef.current.delete(activeSessionId)) {
        setLoadingMessages(false);
      } else if (activeSessionId) void loadTurns(activeSessionId).catch((reason) => setError(String(reason)));
      else {
        setLoadingMessages(false);
      }
    });
  }, [activeSessionId, freshSessionIdsRef, loadTurns]);

  const { send, cancel } = useTurnSender({
    canSend: () => isSelectionReady(settings) && (!(modelProfiles[settings.model_profile]?.supported_levels?.length) || (modelProfiles[settings.model_profile]?.supported_levels ?? []).includes(settings.thinking_level)),
    thinkingSupported: Boolean(modelProfiles[settings.model_profile]?.supported_levels?.length),
    activeSessionRef,
    socketRef,
    pendingRequests,
    pendingDrafts,
    draftOwner: authenticatedUserId,
    inFlightTurnIds,
    cancelledTurnIds,
    preferences,
    settings,
    messages,
    createBackendSession,
    updateSessionMeta,
    setMessages,
    setRequestError,
  });
  const activeMeta = activeSessionId ? preferences.sessions[activeSessionId] ?? {} : {};
  const isCancelling = messages.some((message) => message.role === "assistant" && message.status === "cancelling");
  const isRunning = messages.some((message) => message.role === "assistant" && ["accepted", "running", "cancelling"].includes(message.status ?? ""));

const authenticate = useCallback(async (username: string, password: string) => {
  const result = globalAuth
    ? await globalAuth.login(username, password)
    : await api.login(username, password);

  setError("");
  setRequestError(null);
  return result;
}, [globalAuth]);
  const retryAuthentication = useCallback(() => {
    setError("");
    setRequestError(null);
    setBootStatus("loading");
    cancelBootstrap();
    setAuthRevision((current) => current + 1);
  }, [cancelBootstrap]);

  const logout = useCallback(async () => {
    cancelBootstrap();
    clearRejectedDrafts(authenticatedUserId);
    clearSettings();
    setBootStatus("unauthenticated");
    clearImportedFiles(authSession?.user_id ?? null, workspaceId);
    try {
      if (globalAuth) await globalAuth.logout();
      else await api.logout();
    } catch (err) {
      console.error("Logout failed:", err);
    } finally {
      socketRef.current?.close();
      setAuthSession(null);
      setSessions([]);
      setActiveSessionId(null);
      setMessages([]);
      setModelProfiles({});
      setBootStatus("unauthenticated");
      // Redirect to login page
      window.location.href = "/";
    }
  }, [authSession, authenticatedUserId, cancelBootstrap, clearSettings, globalAuth, setActiveSessionId, setSessions, workspaceId]);

  return {
    sessions,
    workspaceId,
    activeSessionId,
    composerRevision,
    setActiveSessionId,
    selectSession,
    messages,
    preferences,
    activeMeta,
    settings,
    modelProfiles,
    supportedThinkingLevels: modelProfiles[settings.model_profile]?.supported_levels ?? [],
    settingsError,
    selectionPending,
    selectionSaving,
    setSelectionPreview,
    bootStatus,
    error,
    requestError,
    clearRequestError: () => setRequestError(null),
    dismissRejected: (id: string) => {
      if (activeSessionId) removeRejectedDraft(authenticatedUserId, activeSessionId, id);
      setMessages((current) => current.filter((message) => message.id !== id || message.status !== "failed"));
    },
    socketStatus,
    loadingMessages,
    isRunning,
    isCancelling,
    startNewChat,
    ensureSession: createBackendSession,
    send,
    cancel,
    deleteSession,
    renameSessionTitle,
    updateSessionMeta,
    setLearningContext,
    addCategory,
    renameCategory,
    deleteCategory,
    patchSettings,
    resetSettings,
    refresh: loadSessions,
    authenticate,
    retryAuthentication,
    authSession,
    logout,
  };
}
