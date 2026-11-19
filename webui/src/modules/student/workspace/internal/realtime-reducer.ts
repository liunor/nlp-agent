import type { Dispatch, MutableRefObject, SetStateAction } from "react";

import { extractConcepts } from "@/platform/storage/learning-preferences";
import { StudentSocket } from "@/platform/realtime/client";
import { saveRejectedDraft } from "./rejected-drafts";
import type {
  ActivityItem,
  ActivityKind,
  ChatMessage,
  LearningPreferences,
  RealtimeRequestError,
  ServerEvent,
  SessionLearningMeta,
} from "@/shared/types";

const INTERNAL_ORCHESTRATION_TOOLS = new Set([
  "spawn_worker",
  "send_message",
  "TaskStop",
  "SnipTool",
]);

const READABLE_TOOL_LABELS: Record<string, { running: string; completed: string }> = {
  academic_search: { running: "正在检索学术文献", completed: "已完成学术文献检索" },
  get_knowledge_book_context: { running: "正在查阅课程知识库", completed: "已查阅课程知识库" },
  read_local_file: { running: "正在读取课程资料", completed: "已读取课程资料" },
  web_search: { running: "正在联网检索公开资料", completed: "已完成联网检索" },
  web_fetch: { running: "正在阅读网页内容", completed: "已读取网页内容" },
};

const VALID_ACTIVITY_KINDS = new Set<ActivityKind>([
  "thinking",
  "tool",
  "worker",
  "recovery",
  "search",
  "fetch",
  "commentary",
  "synthesis",
]);

function normalizeActivityKind(raw: unknown): ActivityKind {
  return typeof raw === "string" && VALID_ACTIVITY_KINDS.has(raw as ActivityKind)
    ? (raw as ActivityKind)
    : "worker";
}

function normalizeStepStatus(raw: unknown): ActivityItem["status"] {
  if (raw === "completed" || raw === "error" || raw === "running") return raw;
  return "running";
}

function eventDetail(event: ServerEvent): string | undefined {
  for (const key of ["name", "tool", "node", "detail", "message", "status"]) {
    const value = event.payload[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return undefined;
}

function toolDetail(event: ServerEvent): string | undefined {
  const detail = eventDetail(event);
  if (!detail || ["tool", "tools"].includes(detail.toLowerCase())) return undefined;
  if (INTERNAL_ORCHESTRATION_TOOLS.has(detail)) return undefined;
  return detail;
}

function quotaErrorMessage(event: ServerEvent): string | undefined {
  const messages: Record<string, string> = {
    quota_daily_exhausted: "今日额度已用尽",
    quota_weekly_exhausted: "本周额度已用尽",
    quota_workspace_exhausted: "工作区额度已用尽",
    quota_request_limit: "本次请求超过单次额度限制，请选择更低的思考程度，或联系开发者调整额度。",
    quota_concurrency_limit: "当前并发请求已达上限，请稍后重试",
    quota_model_not_allowed: "当前模型不在可用额度范围内",
    quota_policy_not_found: "暂未配置可用额度策略，请联系开发者",
    admission_denied: "额度校验暂时无法完成，请稍后重试",
  };
  const code = typeof event.payload.code === "string" ? event.payload.code : "";
  return messages[code] ?? undefined;
}

function reconcileCompletedContent(streamed: string, final: string): string {
  if (!final || !streamed || final === streamed) return final || streamed;
  // A completion event may carry the authoritative full response after only
  // part of it was streamed. Accept that monotonic extension. If the payload
  // is unrelated (for example a late fallback error), keep what the learner
  // has already seen instead of replacing it with a surprising jump.
  if (final.startsWith(streamed)) return final;
  return streamed;
}

function activityLabel(event: ServerEvent): (Pick<ActivityItem, "kind" | "label" | "status" | "detail"> & { customId?: string }) | null {
  const rawToolName = typeof event.payload.name === "string" ? event.payload.name.trim() : "";
  if (rawToolName && INTERNAL_ORCHESTRATION_TOOLS.has(rawToolName)) return null;

  const stepId = typeof event.payload.step_id === "string" ? event.payload.step_id.trim() : "";
  const phase = typeof event.payload.phase === "string" ? event.payload.phase.trim() : "";
  if (event.type.startsWith("worker.") && phase === "heartbeat") {
    return null;
  }
  if (event.type.startsWith("worker.") && stepId) {
    const label = typeof event.payload.label === "string" ? event.payload.label.trim() : "";
    if (!label) return null;
    const detail = typeof event.payload.detail === "string" && event.payload.detail.trim()
      ? event.payload.detail.trim()
      : undefined;
    return {
      customId: `step:${stepId}`,
      kind: normalizeActivityKind(event.payload.step_kind),
      label,
      status: normalizeStepStatus(event.payload.step_status),
      detail,
    };
  }
  if (event.type.startsWith("worker.") && phase === "coordinator_resume") {
    return null;
  }

  const detail = eventDetail(event);
  const readableTool = toolDetail(event);
  const mappedTool = readableTool ? READABLE_TOOL_LABELS[readableTool] : undefined;
  if (event.type === "tool.started") {
    return {
      kind: "tool",
      label: mappedTool ? mappedTool.running : "正在使用工具",
      status: "running",
      detail: mappedTool ? undefined : readableTool,
      customId: readableTool ? `tool:${readableTool}` : undefined,
    };
  }
  if (event.type === "tool.progress") {
    return {
      kind: "tool",
      label: mappedTool ? mappedTool.running : "工具正在处理",
      status: "running",
      detail: mappedTool ? undefined : readableTool,
      customId: readableTool ? `tool:${readableTool}` : undefined,
    };
  }
  if (event.type === "tool.completed") {
    return {
      kind: "tool",
      label: mappedTool ? mappedTool.completed : "工具调用完成",
      status: "completed",
      detail: mappedTool ? undefined : readableTool,
      customId: readableTool ? `tool:${readableTool}` : undefined,
    };
  }
  if (event.type === "tool.error") {
    return {
      kind: "tool",
      label: "工具执行失败",
      status: "error",
      detail: mappedTool ? undefined : readableTool,
      customId: readableTool ? `tool:${readableTool}` : undefined,
    };
  }
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
  pendingDrafts?: MutableRefObject<Map<string, { sessionId: string; owner: string | null; message: ChatMessage }>>;
  inFlightTurnIds: MutableRefObject<Set<string>>;
  cancelledTurnIds?: MutableRefObject<Set<string>>;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  setActiveSessionId: Dispatch<SetStateAction<string | null>>;
  setRequestError: Dispatch<SetStateAction<RealtimeRequestError | null>>;
  persistPreferences: (update: (current: LearningPreferences) => LearningPreferences) => void;
  updateSessionMeta: (sessionId: string, patch: Partial<SessionLearningMeta>) => void;
  loadSessions: () => Promise<unknown>;
  loadTurns: (sessionId: string) => Promise<void>;
}

export function createRealtimeEventHandler({
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
}: RealtimeHandlerOptions) {
  return (rawEvent: ServerEvent) => {
    let event = rawEvent;
    if (!event || typeof event.type !== "string" || !event.type) return;
    if (!event.payload || typeof event.payload !== "object" || Array.isArray(event.payload)) event = { ...event, payload: {} };
    if (event.type === "command.error") {
      const requestId = event.request_id ?? (pendingRequests.current.size === 1 ? pendingRequests.current.keys().next().value : undefined);
      if (requestId) {
        const draft = pendingDrafts?.current.get(requestId);
        if (draft && event.payload.code !== "not_found") {
          saveRejectedDraft(draft.owner, draft.sessionId, { ...draft.message, status: "failed" });
        }
        pendingDrafts?.current.delete(requestId);
        const optimisticId = pendingRequests.current.get(requestId);
        if (optimisticId) setMessages((current) => current.map((message) => message.id === optimisticId
          ? { ...message, status: "failed" }
          : message));
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
      setRequestError({
        code: typeof event.payload.code === "string" && event.payload.code.trim() ? event.payload.code : "request_failed",
        message: quotaErrorMessage(event) ?? (typeof event.payload.message === "string" && event.payload.message.trim() ? event.payload.message : "请求未能提交，请稍后重试。"),
      });
      return;
    }
    if (["session.created", "session.deleted", "session.updated"].includes(event.type)) void loadSessions();
    if (event.type === "command.ack" && event.request_id) {
      pendingDrafts?.current.delete(event.request_id);
      const optimisticId = pendingRequests.current.get(event.request_id);
      if (optimisticId) {
        if (event.turn_id) { inFlightTurnIds.current.delete(event.request_id); inFlightTurnIds.current.add(event.turn_id); }
        setMessages((current) => current.map((message) => message.id === optimisticId ? { ...message, turnId: event.turn_id ?? message.turnId } : message));
        pendingRequests.current.delete(event.request_id);
      }
      if (event.payload.command === "chat.send" && event.turn_id) {
        const status = typeof event.payload.status === "string" ? event.payload.status : "accepted";
        const queuePosition = typeof event.payload.queue_position === "number" ? event.payload.queue_position : undefined;
        const waitedS = typeof event.payload.waited_s === "number" ? event.payload.waited_s : undefined;
        setMessages((current) => {
          const id = `${event.turn_id}:assistant`;
          const existing = current.findIndex((message) => message.id === id);
          if (existing >= 0) {
            return current.map((message) => message.id === id
              ? { ...message, status: status as ChatMessage["status"], queuePosition, queueWaitedS: waitedS, queueObservedAt: event.timestamp }
              : message);
          }
          return [...current, {
            id,
            turnId: event.turn_id!,
            role: "assistant",
            content: "",
            status: status as ChatMessage["status"],
            queuePosition,
            queueWaitedS: waitedS,
            queueObservedAt: event.timestamp,
            activities: [],
            createdAt: event.timestamp,
          }];
        });
      }
    }
    if (!event.session_id || event.session_id !== activeSessionRef.current || !event.turn_id) return;
    if (event.type === "stream.gap") void loadTurns(event.session_id);
    if (event.type === "chat.cancelled") cancelledTurnIds?.current.add(event.turn_id);
    if (["chat.completed", "chat.message.completed", "chat.error", "chat.cancelled"].includes(event.type)) {
      inFlightTurnIds.current.delete(event.turn_id);
    }
    const cancellationRequested = cancelledTurnIds?.current.has(event.turn_id) ?? false;
    if ((event.type === "chat.completed" || event.type === "chat.message.completed") && cancellationRequested) return;
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
      if (["cancelled", "completed", "failed", "interrupted"].includes(message.status ?? "")) return next;
      if (message.status === "cancelling" && !["chat.cancelled", "chat.error"].includes(event.type)) return next;
      if (event.type === "chat.accepted") {
        message.status = "accepted";
        message.queuePosition = typeof event.payload.queue_position === "number" ? event.payload.queue_position : undefined;
        message.queueWaitedS = typeof event.payload.waited_s === "number" ? event.payload.waited_s : undefined;
        message.queueObservedAt = event.timestamp;
      }
      const delta = typeof event.payload.delta === "string" ? event.payload.delta : "";
      if (event.type === "chat.delta") {
        message.content += delta;
        if (delta.trim()) {
          message.activitySummary = undefined;
          message.activities = message.activities.map((item) =>
            item.status === "running" && (item.kind === "synthesis" || item.kind === "worker")
              ? { ...item, status: "completed", completedAt: event.timestamp }
              : item,
          );
        }
      }
      if (event.type === "chat.reasoning.delta") message.reasoning = `${message.reasoning ?? ""}${delta}`;
      if (event.type === "chat.started") { message.status = "running"; message.startedAt ??= event.timestamp; }
      if (event.type === "chat.message.completed" || event.type === "chat.completed") {
        const final = typeof event.payload.content === "string" ? event.payload.content : "";
        message.content = reconcileCompletedContent(message.content, final);
        message.status = "completed";
        message.completedAt = event.timestamp;
        message.activitySummary = undefined;
        message.activities = message.activities.map((item) =>
          item.status === "running" ? { ...item, status: "completed", completedAt: event.timestamp } : item,
        );
      }
      if (event.type === "chat.cancelled") {
        message.status = "cancelled";
        message.completedAt = event.timestamp;
        message.activitySummary = undefined;
        message.activities = message.activities.map((item) =>
          item.status === "running" ? { ...item, status: "completed", completedAt: event.timestamp } : item,
        );
      }
      if (event.type === "chat.error") {
        message.status = "failed";
        message.completedAt = event.timestamp;
        message.activitySummary = undefined;
        message.activities = message.activities.map((item) =>
          item.status === "running" ? { ...item, status: "error", completedAt: event.timestamp } : item,
        );
      }
      if (event.type.startsWith("worker.") && event.payload.phase === "heartbeat") {
        const summary = typeof event.payload.summary === "string" ? event.payload.summary.trim() : "";
        if (summary) message.activitySummary = summary;
        const stepId = typeof event.payload.step_id === "string" ? event.payload.step_id.trim() : "";
        if (stepId && summary) {
          const targetKey = `step:${stepId}`;
          const targetIdx = message.activities.findIndex((item) => item.id === targetKey);
          if (targetIdx >= 0 && message.activities[targetIdx].status === "running") {
            message.activities[targetIdx] = { ...message.activities[targetIdx], label: summary };
          }
        }
        message.startedAt ??= event.timestamp;
      }
      const activity = activityLabel(event);
      if (activity) {
        const { customId, ...restActivity } = activity;
        const key = customId ?? (restActivity.kind === "tool" && restActivity.detail ? `tool:${restActivity.detail}` : restActivity.kind);
        const existing = message.activities.findIndex((item) => item.id === key);
        const previous = existing >= 0 ? message.activities[existing] : undefined;
        const item: ActivityItem = {
          id: key,
          ...restActivity,
          startedAt: previous?.startedAt ?? event.timestamp,
          ...(restActivity.status === "running" ? {} : { completedAt: event.timestamp }),
        };
        if (existing >= 0) message.activities[existing] = item;
        else message.activities.push(item);
        if (restActivity.status === "running" || restActivity.kind === "commentary") {
          message.activitySummary = restActivity.label;
        } else if (message.activitySummary === previous?.label) {
          const stillRunning = [...message.activities].reverse().find((entry) => entry.status === "running");
          message.activitySummary = stillRunning?.label;
        }
        message.startedAt ??= event.timestamp;
      }
      next[index] = message;
      return next;
    });
  };
}
