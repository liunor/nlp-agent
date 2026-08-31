import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "@/platform/http/api";
import { useOptionalAuth } from "@/platform/auth/AuthContext";
import { StudentSocket } from "@/platform/realtime/client";
import { clearImportedFiles } from "../../components/importedFiles";
import type { AuthSession, ChatMessage, RuntimeModelProfile } from "@/shared/types";

import { useWorkspaceBootstrap } from "../internal/bootstrap";
import { usePreferencesController } from "../internal/preferences-controller";
import { createRealtimeEventHandler } from "../internal/realtime-reducer";
import { useSessionController } from "../internal/session-controller";
import { useSettingsController } from "../internal/settings-controller";
import { useTurnSender } from "../internal/send-turn";
import { useTurnHistory } from "../internal/turn-history";

export function useStudentWorkspace() {
  const globalAuth = useOptionalAuth();
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
  const { settings, settingsError, initializeSettings, patchSettings, resetSettings } = useSettingsController();
  const [requestError, setRequestError] = useState("");
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
    createBackendSession,
    startNewChat,
    deleteSession,
    renameSessionTitle,
  } = useSessionController({ preferences, persistPreferences, updateSessionMeta, onRequestError: setRequestError });
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [modelProfiles, setModelProfiles] = useState<Record<string, RuntimeModelProfile>>({});
  const [authSession, setAuthSession] = useState<AuthSession | null>(null);
  const [bootStatus, setBootStatus] = useState<"loading" | "ready" | "unauthenticated" | "error">("loading");
  const [authRevision, setAuthRevision] = useState(0);
  const [error, setError] = useState("");
  const [socketStatus, setSocketStatus] = useState<"connecting" | "connected" | "reconnecting" | "offline">("connecting");
  const [loadingMessages, setLoadingMessages] = useState(false);
  const socketRef = useRef<StudentSocket | null>(null);
  const pendingRequests = useRef(new Map<string, string>());
  const inFlightTurnIds = useRef(new Set<string>());
  const loadGenerationRef = useRef(0);

  const loadTurns = useTurnHistory({
    activeSessionRef,
    socketRef,
    preferencesRef,
    inFlightTurnIds,
    loadGenerationRef,
    setMessages,
    setLoadingMessages,
    updateSessionMeta,
  });
  const handleEvent = useMemo(
    // The factory stores refs for the socket callback; it does not read them during render.
    // eslint-disable-next-line react-hooks/refs
    () => createRealtimeEventHandler({
      socketRef,
      activeSessionRef,
      pendingRequests,
      inFlightTurnIds,
      setMessages,
      setActiveSessionId,
      setRequestError,
      persistPreferences,
      updateSessionMeta,
      loadSessions,
      loadTurns,
    }),
    [activeSessionRef, inFlightTurnIds, loadSessions, loadTurns, persistPreferences, setActiveSessionId, socketRef, updateSessionMeta],
  );

  useWorkspaceBootstrap({
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

  useEffect(() => {
    if (bootStatus !== "ready") return;
    const socket = new StudentSocket(handleEvent, setSocketStatus);
    socketRef.current = socket;
    socket.connect();
    return () => {
      socket.close();
      socketRef.current = null;
    };
  }, [bootStatus, handleEvent]);

  useEffect(() => {
    loadGenerationRef.current += 1;
    socketRef.current?.setSession(activeSessionId);
    queueMicrotask(() => {
      if (activeSessionId && freshSessionIdsRef.current.delete(activeSessionId)) {
        setLoadingMessages(false);
      } else if (activeSessionId) void loadTurns(activeSessionId).catch((reason) => setError(String(reason)));
      else {
        setMessages([]);
        setLoadingMessages(false);
      }
    });
  }, [activeSessionId, freshSessionIdsRef, loadTurns]);

  const { send, cancel } = useTurnSender({
    activeSessionRef,
    socketRef,
    pendingRequests,
    inFlightTurnIds,
    preferences,
    settings,
    messages,
    createBackendSession,
    updateSessionMeta,
    setMessages,
    setRequestError,
  });
  const activeMeta = activeSessionId ? preferences.sessions[activeSessionId] ?? {} : {};
  const isRunning = messages.some((message) => message.role === "assistant" && ["accepted", "running"].includes(message.status ?? ""));

  const retryAuthentication = useCallback(() => {
    setError("");
    setBootStatus("loading");
    setAuthRevision((current) => current + 1);
  }, []);
  const logout = useCallback(async () => {
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
  }, [authSession, globalAuth, setActiveSessionId, setSessions, workspaceId]);

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
    settingsError,
    bootStatus,
    error,
    requestError,
    clearRequestError: () => setRequestError(""),
    socketStatus,
    loadingMessages,
    isRunning,
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
    retryAuthentication,
    authSession,
    logout,
  };
}
