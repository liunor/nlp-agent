export interface Overview { period_days: number; requests: number; successes: number; errors: number; error_rate: number; latency_ms: { p50: number | null; p95: number | null }; ttft_ms: { p50: number | null; p95: number | null }; tokens: Record<string, number>; runtime: Record<string, unknown>; }
export interface Trace { trace_id: string; request_id: string; session_id: string; turn_id: string; workspace_id: string; user_id: string; channel: string; source: string; started_at: string; completed_at?: string; duration_ms?: number; ttft_ms?: number; status: string; input_tokens: number; output_tokens: number; cached_tokens: number; cache_miss_tokens: number; reasoning_tokens: number; total_tokens: number; error_kind?: string; error_message?: string; attributes: Record<string, unknown>; }
export interface Span { span_id: string; parent_span_id?: string; worker_id?: string; kind: string; name: string; started_at: string; completed_at?: string; duration_ms?: number; status: string; attempt: number; total_tokens: number; input_tokens: number; output_tokens: number; cached_tokens: number; error_kind?: string; error_message?: string; attributes: Record<string, unknown>; }
export interface TelemetryEvent { event_id: string; timestamp: string; level: string; name: string; trace_id?: string; span_id?: string; session_id?: string; turn_id?: string; worker_id?: string; payload: Record<string, unknown>; }
export interface TraceDetail { trace: Trace; spans: Span[]; events: TelemetryEvent[]; }
export interface UsageRow { day: string; component: string; name: string; requests: number; successes: number; errors: number; duration_sum_ms: number; input_tokens: number; output_tokens: number; cached_tokens: number; cache_miss_tokens: number; reasoning_tokens: number; total_tokens: number; }
export interface SessionRow { session_id: string; workspace_id: string; user_id: string; channel: string; turns: number; errors: number; avg_duration_ms: number; total_tokens: number; last_seen: string; }
export interface ErrorRow { error_kind: string; kind: string; name: string; count: number; last_seen: string; sample_trace_id: string; }
export type { SandboxExecution, SandboxLogEntry, SandboxOverview, SandboxRuntime } from "./SandboxMonitorPage";
import type { SandboxExecution, SandboxLogEntry, SandboxOverview, SandboxRuntime } from "./SandboxMonitorPage";
import type { AuthorizationAuditListResponse, AuthorizationAuditSummary } from "@/shared/types";

let csrf = "";
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers); if (init.method && init.method !== "GET" && csrf) headers.set("X-CSRF-Token", csrf);
  const response = await fetch(`/api/v1${path}`, { ...init, headers, credentials: "include" });
  if (!response.ok) { const body = await response.json().catch(() => ({})) as { title?: string }; throw new Error(body.title ?? `HTTP ${response.status}`); }
  return response.json() as Promise<T>;
}
export async function authenticate() { const result = await request<{ csrf_token: string }>("/auth/session"); csrf = result.csrf_token; }
export const monitorApi = {
  createWsTicket: () => request<{ ticket: string; expires_in: number }>("/auth/ws-ticket", { method: "POST", body: "{}" }),
  authorizationAudit: (params: { limit?: number; offset?: number; actorUserId?: string; decision?: string; reasonCode?: string } = {}) => {
    const query = new URLSearchParams();
    query.set("limit", String(params.limit ?? 50));
    query.set("offset", String(params.offset ?? 0));
    if (params.actorUserId) query.set("actor_user_id", params.actorUserId);
    if (params.decision) query.set("decision", params.decision);
    if (params.reasonCode) query.set("reason_code", params.reasonCode);
    return request<AuthorizationAuditListResponse>(`/audit/authorization?${query.toString()}`);
  },
  authorizationAuditStats: (days = 30) => request<AuthorizationAuditSummary>(`/audit/authorization/stats?days=${days}`),
  overview: (days: number) => request<Overview>(`/observability/overview?days=${days}`),
  traces: (limit = 200) => request<{ items: Trace[] }>(`/observability/traces?limit=${limit}`),
  trace: (id: string) => request<TraceDetail>(`/observability/traces/${encodeURIComponent(id)}`),
  usage: (days: number) => request<{ items: UsageRow[] }>(`/observability/usage?days=${days}`),
  sessions: (days: number) => request<{ items: SessionRow[] }>(`/observability/sessions?days=${days}&limit=200`),
  events: (limit = 300) => request<{ items: TelemetryEvent[] }>(`/observability/events?limit=${limit}`),
  errors: (days: number) => request<{ items: ErrorRow[] }>(`/observability/errors?days=${days}&limit=200`),
  storage: () => request<Record<string, unknown>>("/observability/storage"),
  sandboxOverview: () => request<SandboxOverview>("/observability/sandbox/overview"),
  sandboxLogs: (limit = 80, sinceSeconds = 600) => request<{ items: SandboxLogEntry[]; retention_seconds: number; sampled_at: string }>(`/observability/sandbox/logs?limit=${limit}&since_seconds=${sinceSeconds}`),
  sandboxRuntimes: () => request<{ items: SandboxRuntime[] }>("/observability/sandbox/runtimes"),
  sandboxRuntime: (runtimeId: string) => request<SandboxRuntime>(`/observability/sandbox/runtimes/${encodeURIComponent(runtimeId)}`),
  drainSandboxRuntime: (runtimeId: string) => request<{ id: string; state: string }>(`/observability/sandbox/runtimes/${encodeURIComponent(runtimeId)}/drain`, { method: "POST", body: "{}" }),
  sandboxExecutions: (status?: string) => request<{ items: SandboxExecution[] }>(`/observability/sandbox/executions${status ? `?status_filter=${encodeURIComponent(status)}` : ""}`),
  sandboxExecutionEvents: (executionId: string, afterEventId?: string) => request<{ execution_id: string; events: Array<{ event_id: string; seq: number | string; type: string; payload: Record<string, unknown> }> }>(`/observability/sandbox/executions/${encodeURIComponent(executionId)}/events${afterEventId ? `?after_event_id=${encodeURIComponent(afterEventId)}` : ""}`),
  prune: (traceDays: number, eventDays: number) => request<Record<string, unknown>>(`/observability/storage/prune?trace_days=${traceDays}&event_days=${eventDays}`, { method: "POST" }),
  reset: () => request<Record<string, unknown>>("/observability/storage/reset", { method: "POST" }),
};
