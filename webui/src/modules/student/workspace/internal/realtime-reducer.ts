import type { Dispatch, MutableRefObject, SetStateAction } from "react";

import { extractConcepts } from "@/platform/storage/learning-preferences";
import { StudentSocket } from "@/platform/realtime/client";
import type {
  ActivityItem,
  ChatMessage,
  LearningPreferences,
  ServerEvent,
  SessionLearningMeta,
} from "@/shared/types";

function eventDetail(event: ServerEvent): string | undefined {
  for (const key of ["name", "tool", "node", "detail", "message", "status"]) {
    const value = event.payload[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return undefined;
}

function toolDetail(event: ServerEvent): string | undefined {
  const detail = eventDetail(event);
  return detail && !["tool", "tools"].includes(detail.toLowerCase()) ? detail : undefined;
}

function quotaErrorMessage(event: ServerEvent): string | undefined {
  const messages: Record<string, string> = {
    quota_daily_exhausted: "今日额度已用尽",
    quota_weekly_exhausted: "本周额度已用尽",
    quota_workspace_exhausted: "工作区额度已用尽",
    quota_request_limit: "本次请求预计超过单次额度限制",
    quota_concurrency_limit: "当前并发请求已达上限，请稍后重试",
    quota_model_not_allowed: "当前模型不在可用额度范围内",
    quota_policy_not_found: "暂未配置可用额度策略，请联系开发者",
    admission_denied: "额度校验暂时无法完成，请稍后重试",
  };
  const code = typeof event.payload.code === "string" ? event.payload.code : "";
  return messages[code] ?? undefined;
}

function activityLabel(event: ServerEvent): Pick<ActivityItem, "kind" | "label" | "status" | "detail"> | null {
  const detail = eventDetail(event);
  const readableTool = toolDetail(event);
  if (event.type === "tool.started") return { kind: "tool", label: "正在使用工具", status: "running", detail: readableTool };
  if (event.type === "tool.progress") return { kind: "tool", label: "工具正在处理", status: "running", detail: readableTool };
  if (event.type === "tool.completed") return { kind: "tool", label: "工具调用完成", status: "completed", detail: readableTool };
  if (event.type === "tool.error") return { kind: "tool", label: "工具执行失败", status: "error", detail: readableTool };
  if (event.type === "worker.started") return { kind: "worker", label: "教学助手正在分解问题", status: "running", detail };
  if (event.type === "worker.progress") return { kind: "worker", label: "教学助手正在协作", status: "running", detail };
  if (event.type === "worker.completed") return { kind: "worker", label: "教学助手已完成分析", status: "completed", detail };
  if (event.type === "worker.error") return { kind: "worker", label: "教学助手未能完成任务", status: "error", detail };
  if (event.type === "stream.gap") return { kind: "recovery", label: "部分实时过程已过期，已从最终记录恢复", status: "completed" };
  return null;
}

interface RealtimeHandlerOptions {
  socketRef: MutableRefObject<StudentSocket | null>;
  activeSessionRef: MutableRefObject<string | null>;
  pendingRequests: MutableRefObject<Map<string, string>>;
  inFlightTurnIds: MutableRefObject<Set<string>>;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  setActiveSessionId: Dispatch<SetStateAction<string | null>>;
  setRequestError: Dispatch<SetStateAction<string>>;
  persistPreferences: (update: (current: LearningPreferences) => LearningPreferences) => void;
  updateSessionMeta: (sessionId: string, patch: Partial<SessionLearningMeta>) => void;
  loadSessions: () => Promise<unknown>;
  loadTurns: (sessionId: string) => Promise<void>;
}

export function createRealtimeEventHandler({
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
}: RealtimeHandlerOptions) {
  return (rawEvent: ServerEvent) => {
    let event = rawEvent;
    if (!event || typeof event.type !== "string" || !event.type) return;
    if (!event.payload || typeof event.payload !== "object" || Array.isArray(event.payload)) event = { ...event, payload: {} };
    if (event.type === "command.error") {
      const requestId = event.request_id ?? (pendingRequests.current.size === 1 ? pendingRequests.current.keys().next().value : undefined);
      if (requestId) {
        const optimisticId = pendingRequests.current.get(requestId);
        if (optimisticId) setMessages((current) => current.filter((message) => message.id !== optimisticId));
        pendingRequests.current.delete(requestId);
        inFlightTurnIds.current.delete(requestId);
      }
      if (event.payload.code === "not_found" && event.session_id) {
        const sessionId = event.session_id;
        socketRef.current?.setSession(null);
        pendingRequests.current.clear();
        inFlightTurnIds.current.clear();
        if (activeSessionRef.current === sessionId) {
          setActiveSessionId(null);
          setMessages([]);
        }
        persistPreferences((current) => {
          const sessions = { ...current.sessions };
          delete sessions[sessionId];
          return { ...current, sessions };
        });
        void loadSessions();
        return;
      }
      setRequestError(quotaErrorMessage(event) ?? (typeof event.payload.message === "string" ? event.payload.message : "请求未能提交，请稍后重试。"));
      return;
    }
    if (["session.created", "session.deleted", "session.updated"].includes(event.type)) void loadSessions();
    if (event.type === "command.ack" && event.request_id) {
      const optimisticId = pendingRequests.current.get(event.request_id);
      if (optimisticId) {
        if (event.turn_id) { inFlightTurnIds.current.delete(event.request_id); inFlightTurnIds.current.add(event.turn_id); }
        setMessages((current) => current.map((message) => message.id === optimisticId ? { ...message, turnId: event.turn_id ?? message.turnId } : message));
        pendingRequests.current.delete(event.request_id);
      }
    }
    if (!event.session_id || event.session_id !== activeSessionRef.current || !event.turn_id) return;
    if (event.type === "stream.gap") void loadTurns(event.session_id);
    if (event.type === "chat.completed" && typeof event.payload.content === "string") {
      updateSessionMeta(event.session_id, {
        summary: event.payload.content.replace(/[#*_`]/g, "").slice(0, 180),
        concepts: extractConcepts(event.payload.content),
      });
      // Refresh the sidebar so the backend-generated title is picked up. The
      // summary is generated asynchronously after turn completion, so the
      // immediate refresh races it; a delayed refresh surfaces the title once
      // the background write has landed.
      void loadSessions();
      window.setTimeout(() => void loadSessions(), 2500);
    }
    setMessages((current) => {
      const next = [...current];
      const assistantId = `${event.turn_id}:assistant`;
      let index = next.findIndex((message) => message.id === assistantId);
      const ensureAssistant = () => {
        if (index >= 0) return;
        next.push({ id: assistantId, turnId: event.turn_id!, role: "assistant", content: "", status: "running", activities: [], createdAt: event.timestamp, startedAt: event.type === "chat.started" ? event.timestamp : undefined });
        index = next.length - 1;
      };
      if (event.type.startsWith("chat.") || event.type.startsWith("tool.") || event.type.startsWith("worker.") || event.type === "stream.gap") ensureAssistant();
      if (index < 0) return next;
      const message = { ...next[index], activities: [...(next[index].activities ?? [])] };
      const delta = typeof event.payload.delta === "string" ? event.payload.delta : "";
      if (event.type === "chat.delta") message.content += delta;
      if (event.type === "chat.reasoning.delta") message.reasoning = `${message.reasoning ?? ""}${delta}`;
      if (event.type === "chat.started") { message.status = "running"; message.startedAt ??= event.timestamp; }
      if (event.type === "chat.message.completed" || event.type === "chat.completed") { const final = typeof event.payload.content === "string" ? event.payload.content : ""; if (final) message.content = final; message.status = "completed"; message.completedAt = event.timestamp; }
      if (event.type === "chat.cancelled") { message.status = "cancelled"; message.completedAt = event.timestamp; }
      if (event.type === "chat.error") { message.status = "failed"; message.completedAt = event.timestamp; }
      const activity = activityLabel(event);
      if (activity) {
        const key = activity.kind === "tool" && activity.detail ? `tool:${activity.detail}` : activity.kind;
        const existing = message.activities.findIndex((item) => item.id === key);
        const previous = existing >= 0 ? message.activities[existing] : undefined;
        const item: ActivityItem = { id: key, ...activity, startedAt: previous?.startedAt ?? event.timestamp, ...(activity.status === "running" ? {} : { completedAt: event.timestamp }) };
        if (existing >= 0) message.activities[existing] = item;
        else message.activities.push(item);
        message.startedAt ??= event.timestamp;
      }
      next[index] = message;
      return next;
    });
  };
}
