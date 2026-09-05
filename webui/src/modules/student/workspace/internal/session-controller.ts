import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";

import { api } from "@/platform/http/api";
import type { LearningPreferences, SessionLearningMeta, SessionSummary } from "@/shared/types";

interface SessionControllerOptions {
  preferences: LearningPreferences;
  persistPreferences: (update: (current: LearningPreferences) => LearningPreferences) => void;
  updateSessionMeta: (sessionId: string, patch: Partial<SessionLearningMeta>) => void;
  onRequestError: (message: string) => void;
}

export function useSessionController({ preferences, persistPreferences, updateSessionMeta, onRequestError }: SessionControllerOptions) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [workspaceId, setWorkspaceId] = useState("default");
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [composerRevision, setComposerRevision] = useState(0);
  const activeSessionRef = useRef<string | null>(null);
  const creationRef = useRef<Promise<string | null> | null>(null);
  const freshSessionIdsRef = useRef(new Set<string>());
  const chatEpochRef = useRef(0);

  useEffect(() => {
    activeSessionRef.current = activeSessionId;
  }, [activeSessionId]);

  const loadSessions = useCallback(async () => {
    const response = await api.listSessions();
    setSessions(response.items);
    const existing = new Set(response.items.map((session) => session.session_id));
    persistPreferences((current) => {
      const sessions = Object.fromEntries(
        Object.entries(current.sessions).filter(([sessionId]) => existing.has(sessionId)),
      );
      return Object.keys(sessions).length === Object.keys(current.sessions).length
        ? current
        : { ...current, sessions };
    });
    return response.items;
  }, [persistPreferences]);

  const createBackendSession = useCallback(() => {
    if (creationRef.current) return creationRef.current;
    const epoch = chatEpochRef.current;
    const creation = (async () => {
      const session = await api.createSession(workspaceId);
      if (epoch !== chatEpochRef.current) {
        // A new chat reset the workspace while this creation was in flight;
        // drop the empty session and signal the caller to abort its send.
        // Returning null keeps the pending send from publishing an optimistic
        // turn into a session that no longer belongs to the active chat.
        void api.deleteSession(session.session_id).catch(() => undefined);
        return null;
      }
      freshSessionIdsRef.current.add(session.session_id);
      setSessions((current) => current.some((item) => item.session_id === session.session_id) ? current : [session, ...current]);
      updateSessionMeta(session.session_id, { topic: preferences.context.topic_name });
      activeSessionRef.current = session.session_id;
      setActiveSessionId(session.session_id);
      return session.session_id;
    })();
    creationRef.current = creation;
    void creation.then(
      () => { if (creationRef.current === creation) creationRef.current = null; },
      () => { if (creationRef.current === creation) creationRef.current = null; },
    );
    return creation;
  }, [preferences.context.topic_name, updateSessionMeta, workspaceId]);

  const selectSession = useCallback((sessionId: string) => {
    if (activeSessionRef.current === sessionId) return;
    chatEpochRef.current += 1;
    creationRef.current = null;
    activeSessionRef.current = sessionId;
    setComposerRevision((current) => current + 1);
    setActiveSessionId(sessionId);
  }, []);

  const startNewChat = useCallback(() => {
    chatEpochRef.current += 1;
    creationRef.current = null;
    activeSessionRef.current = null;
    setComposerRevision((current) => current + 1);
    setActiveSessionId(null);
  }, []);

  const deleteSession = useCallback(async (sessionId: string) => {
    await api.deleteSession(sessionId);
    const remaining = sessions.filter((session) => session.session_id !== sessionId);
    setSessions(remaining);
    persistPreferences((current) => {
      const nextSessions = { ...current.sessions };
      delete nextSessions[sessionId];
      return { ...current, sessions: nextSessions };
    });
    if (activeSessionRef.current === sessionId) {
      const nextSessionId = remaining[0]?.session_id ?? null;
      activeSessionRef.current = nextSessionId;
      setComposerRevision((current) => current + 1);
      setActiveSessionId(nextSessionId);
    }
  }, [persistPreferences, sessions]);

  const renameSessionTitle = useCallback(async (sessionId: string, title: string) => {
    try {
      const renamed = await api.renameSession(sessionId, title);
      setSessions((current) => current.map((session) => session.session_id === sessionId ? { ...session, title: renamed.title, title_is_manual: true } : session));
      persistPreferences((current) => {
        const meta = current.sessions[sessionId];
        if (!meta?.title) return current;
        const next = { ...meta };
        delete next.title;
        return { ...current, sessions: { ...current.sessions, [sessionId]: next } };
      });
    } catch (error) {
      // The sidebar fires rename fire-and-forget; swallow the rejection here so
      // it never becomes an unhandled promise rejection, and surface the failure.
      onRequestError(`重命名失败：${error instanceof Error ? error.message : String(error)}`);
    }
  }, [onRequestError, persistPreferences]);

  return {
    sessions,
    setSessions,
    workspaceId,
    setWorkspaceId,
    activeSessionId,
    composerRevision,
    setActiveSessionId,
    selectSession,
    activeSessionRef: activeSessionRef as MutableRefObject<string | null>,
    freshSessionIdsRef: freshSessionIdsRef as MutableRefObject<Set<string>>,
    loadSessions,
    createBackendSession,
    startNewChat,
    deleteSession,
    renameSessionTitle,
  };
}
