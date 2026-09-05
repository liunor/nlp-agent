import { useCallback, type Dispatch, type MutableRefObject, type SetStateAction } from "react";

import { StudentSocket } from "@/platform/realtime/client";
import type { ChatAttachment, ChatMessage, LearningPreferences, SessionLearningMeta, UserSettings } from "@/shared/types";
import { createUuid } from "@/shared/utils/uuid";

interface TurnSenderOptions {
  activeSessionRef: MutableRefObject<string | null>;
  socketRef: MutableRefObject<StudentSocket | null>;
  pendingRequests: MutableRefObject<Map<string, string>>;
  inFlightTurnIds: MutableRefObject<Set<string>>;
  preferences: LearningPreferences;
  settings: UserSettings;
  messages: ChatMessage[];
  createBackendSession: () => Promise<string | null>;
  updateSessionMeta: (sessionId: string, patch: Partial<SessionLearningMeta>) => void;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  setRequestError: Dispatch<SetStateAction<string>>;
}

export function useTurnSender({
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
}: TurnSenderOptions) {
  const send = useCallback(async (content: string, attachments?: ChatAttachment[]) => {
    setRequestError("");
    const requestId = createUuid();
    inFlightTurnIds.current.add(requestId);
    pendingRequests.current.set(requestId, "");
    let sessionId = activeSessionRef.current;
    if (!sessionId) sessionId = await createBackendSession();
    if (!sessionId || !pendingRequests.current.has(requestId)) {
      // The session creation was abandoned by a newer chat (createBackendSession
      // returned null) or the request was cancelled while awaiting; drop it.
      pendingRequests.current.delete(requestId);
      inFlightTurnIds.current.delete(requestId);
      return;
    }
    const optimisticId = `${requestId}:user`;
    pendingRequests.current.set(requestId, optimisticId);
    setMessages((current) => [...current, {
      id: optimisticId,
      turnId: requestId,
      role: "user",
      content: content.trim(),
      ...(attachments?.length ? { attachments: attachments.map((attachment) => ({
        fileName: attachment.fileName,
        displayName: attachment.displayName,
        url: attachment.url,
        mediaType: attachment.mediaType,
        width: attachment.width,
        height: attachment.height,
        status: "ready" as const,
      })) } : {}),
      createdAt: new Date().toISOString(),
    }]);
    const currentMeta = preferences.sessions[sessionId];
    if (!currentMeta?.topic) {
      updateSessionMeta(sessionId, { topic: preferences.context.topic_name });
    }
    socketRef.current?.setSession(sessionId);
    socketRef.current?.sendChat(
      sessionId,
      content.trim(),
      requestId,
      preferences.context,
      settings.model_profile,
      attachments?.map((attachment) => ({ file_name: attachment.fileName })),
    );
  }, [activeSessionRef, createBackendSession, inFlightTurnIds, pendingRequests, preferences.context, preferences.sessions, setMessages, setRequestError, settings.model_profile, socketRef, updateSessionMeta]);

  const cancel = useCallback(() => {
    const running = [...messages].reverse().find((message) => message.role === "assistant" && ["accepted", "running"].includes(message.status ?? ""));
    if (running) socketRef.current?.cancel(running.turnId);
  }, [messages, socketRef]);

  return { send, cancel };
}
