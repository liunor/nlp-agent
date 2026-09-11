export interface OverviewDimension { value: string; requests: number; }
export interface OverviewComponent { name: string; label: string; requests: number; successes: number; errors: number; failed_requests: number; error_rate: number; failure_rate: number; retries: number; avg_duration_ms: number; total_tokens: number; }
export interface OverviewModel { provider: string; provider_model: string; model_profile: string; requests: number; successes: number; errors: number; error_rate: number; retries: number; avg_duration_ms: number; total_tokens: number; }
export interface OverviewUser { user_id: string; requests: number; successes: number; errors: number; error_rate: number; total_tokens: number; avg_duration_ms: number; workspaces: string[]; last_seen: string | null; }
export interface OverviewError { error_kind: string; kind: string; name: string; count: number; last_seen: string | null; sample_trace_id: string; }
export interface PercentileMetrics { p50: number | null; p90: number | null; p95: number | null; p99: number | null; }
export interface AnalysisBounds { truncated: boolean; row_limit: number; }
export interface Overview { period_days: number; from?: string; to?: string; requests: number; successes: number; errors: number; failed_requests?: number; error_rate: number; failure_rate?: number; active_users: number; active_workspaces: number; active_sessions: number; latency_ms: PercentileMetrics; ttft_ms: PercentileMetrics; tokens: Record<string, number>; runtime: Record<string, unknown>; analysis?: AnalysisBounds; status_breakdown: OverviewDimension[]; tags: { channels: OverviewDimension[]; sources: OverviewDimension[]; span_kinds: OverviewDimension[] }; component_spans: OverviewComponent[]; span_kinds: OverviewComponent[]; models: OverviewModel[]; error_groups: OverviewError[]; events_by_level: Record<string, number>; event_names: OverviewDimension[]; top_users: OverviewUser[]; latest_trace_at?: string | null; latest_event_at?: string | null; }
export interface DependencyHealthRow { label?: string; kind?: string; name?: string; provider?: string; provider_model?: string; model_profile?: string; requests: number; successes: number; errors: number; failed_requests: number; error_rate: number; retries: number; total_tokens: number; latency_ms: PercentileMetrics; ttft_ms: PercentileMetrics; users: number; workspaces: number; error_kinds: string[]; first_seen: string | null; last_seen: string | null; status: "healthy" | "watch" | "degraded"; }
export interface DependencyHealthSummary { requests: number; component_calls: number; errors: number; component_errors: number; error_rate: number; active_users: number; active_workspaces: number; latency_ms: PercentileMetrics; ttft_ms: PercentileMetrics; total_tokens: number; }
export interface OperationalTrendPoint { period_start: string; period_end: string; granularity: string; requests: number; component_calls: number; errors: number; component_errors: number; error_rate: number; retries: number; total_tokens: number; component_tokens: number; active_users: number; latency_ms: PercentileMetrics; component_latency_ms: PercentileMetrics; ttft_ms: PercentileMetrics; }
export interface DependencyHealth { scope: "system"; period_days: number; from: string; to: string; summary: DependencyHealthSummary; trend: OperationalTrendPoint[]; components: DependencyHealthRow[]; providers: DependencyHealthRow[]; models: DependencyHealthRow[]; anomalies: Array<{ type: string; key: string; status: string; errors: number; error_rate: number; p95_ms: number | null; last_seen: string | null }>; analysis?: AnalysisBounds; catalog?: SystemUsageCatalog; }
export interface ErrorAnalysisSummary { error_groups: number; total_errors: number; affected_requests: number; affected_users: number; ongoing_groups: number; recovered_groups: number; error_rate: number; }
export interface ErrorAnalysis { scope: "system"; period_days: number; from: string; to: string; summary: ErrorAnalysisSummary; trend: OperationalTrendPoint[]; items: ErrorRow[]; total: number; offset: number; limit: number; has_more: boolean; analysis?: AnalysisBounds; }
export interface Trace { trace_id: string; request_id: string; session_id: string; turn_id: string; workspace_id: string; user_id: string; channel: string; source: string; started_at: string; completed_at?: string; duration_ms?: number; ttft_ms?: number; status: string; input_tokens: number; output_tokens: number; cached_tokens: number; cache_miss_tokens: number; reasoning_tokens: number; total_tokens: number; error_kind?: string; error_message?: string; attributes: Record<string, unknown>; }
export interface Span { span_id: string; parent_span_id?: string; worker_id?: string; kind: string; name: string; started_at: string; completed_at?: string; duration_ms?: number; ttft_ms?: number; status: string; attempt: number; total_tokens: number; input_tokens: number; output_tokens: number; cached_tokens: number; error_kind?: string; error_message?: string; attributes: Record<string, unknown>; }
export interface TelemetryEvent { event_id: string; timestamp: string; level: string; name: string; trace_id?: string; span_id?: string; session_id?: string; turn_id?: string; worker_id?: string; payload: Record<string, unknown>; }
export interface TraceDetail { trace: Trace; spans: Span[]; events: TelemetryEvent[]; detail_limits?: { children: number; children_truncated?: boolean }; }
export interface TraceGroupSummary {
  chain_id: string;
  chain_name: string;
  entrypoint: string;
  user_ids: string[];
  workspace_ids: string[];
  sources: string[];
  started_at: string;
  last_seen: string;
  status: string;
  trace_count: number;
  error_count: number;
  total_duration_ms: number;
  max_duration_ms?: number;
  slow_trace_count?: number;
  total_tokens: number;
  span_count?: number;
  failed_span_count?: number;
  slow_span_count?: number;
  sample_trace_id: string;
  error_kinds: string[];
  provider_models?: string[];
  component_names?: string[];
}
export interface TraceGroupPage { scope: "system"; items: TraceGroupSummary[]; total: number; offset: number; limit: number; has_more: boolean; period_days?: number; analysis?: AnalysisBounds; }
export interface TraceGroupDetail { chain: TraceGroupSummary; traces: Trace[]; spans: Array<Span & { trace_id?: string }>; events: TelemetryEvent[]; detail_limits?: { traces?: number; children: number; traces_truncated?: boolean; children_truncated?: boolean }; }
export interface UsageRow { day: string; component: string; name: string; requests: number; successes: number; errors: number; duration_sum_ms: number; input_tokens: number; output_tokens: number; cached_tokens: number; cache_miss_tokens: number; reasoning_tokens: number; total_tokens: number; }
export interface ErrorRow { fingerprint?: string; error_kind: string; kind: string; name: string; count: number; trace_count?: number; affected_users?: number; affected_workspaces?: number; first_seen?: string | null; last_seen: string | null; latency_ms?: PercentileMetrics; provider_models?: string[]; chains?: string[]; recovery_status?: "ongoing" | "recovered" | "stale"; sample_trace_id: string; }
export interface SystemUsageDimension { user_id?: string; workspace_id?: string; provider?: string; purpose?: string; provider_model?: string; events: number; priced_events: number; unpriced_events: number; credits_complete: boolean; credit_status: string; credits_micro: number | null; priced_credits_micro: number; tokens: Record<string, number>; }
export interface SystemUsageBreakdown { day: string; period_start?: string; period_end?: string; granularity?: string; purpose?: string; provider?: string; provider_model?: string; events?: number; priced_events?: number; unpriced_events?: number; priced_credits_micro?: number; total_tokens?: number; tokens?: Record<string, number>; }
export interface SystemUsageProviderCatalog { adapter?: string; base_url?: string; api_key_configured?: boolean; }
export interface SystemUsageModelCatalog { catalog_name?: string; provider?: string; model_id?: string; context_window_tokens?: number; capabilities?: Record<string, unknown>; profile_names?: string[]; }
export interface SystemUsageCatalog { providers: Record<string, SystemUsageProviderCatalog>; models: Record<string, SystemUsageModelCatalog>; }
export interface SystemUsageSnapshot { scope: "system"; period_days: number; from: string; to: string; granularity: string; events: number; priced_events: number; unpriced_events: number; credits_complete: boolean; credit_status: string; credits_micro: number | null; priced_credits_micro: number; tokens: Record<string, number>; cache_hit_rate?: number | null; cache_input_tokens?: number | null; cache_cached_input_tokens?: number | null; breakdown: SystemUsageBreakdown[]; users: SystemUsageDimension[]; workspaces: SystemUsageDimension[]; providers: SystemUsageDimension[]; purposes: SystemUsageDimension[]; models: SystemUsageDimension[]; catalog?: SystemUsageCatalog; window_minutes?: number; bucket_minutes?: number; }
export interface SystemUsageUserPage { scope: "system"; period_days: number; from: string; to: string; items: SystemUsageDimension[]; total: number; offset: number; limit: number; has_more: boolean; }
export interface SystemUsageDimensionPage extends SystemUsageUserPage { dimension: "users" | "workspaces" | "providers" | "purposes" | "models"; }
export interface MonitorSession { user_id: string; roles: string[]; permissions: string[]; csrf_token: string; expires_at: number; }
export type { SandboxExecution, SandboxLogEntry, SandboxOverview, SandboxPage, SandboxRuntime } from "./SandboxMonitorPage";
import type { SandboxExecution, SandboxLogEntry, SandboxOverview, SandboxPage, SandboxRuntime } from "./SandboxMonitorPage";
import type { AuthorizationAuditListResponse, AuthorizationAuditSummary } from "@/shared/types";

let csrf = "";
export class MonitorApiError extends Error {
  constructor(message: string, readonly status: number, readonly code?: string) {
    super(message);
    this.name = "MonitorApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (init.method && init.method !== "GET" && csrf) headers.set("X-CSRF-Token", csrf);
  const response = await fetch(`/api/v1${path}`, { ...init, headers, credentials: "include" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { title?: string; detail?: string; code?: string };
    throw new MonitorApiError(body.title ?? body.detail ?? `HTTP ${response.status}`, response.status, body.code);
  }
  return response.json() as Promise<T>;
}
export async function authenticate() { const result = await request<MonitorSession>("/auth/session"); csrf = result.csrf_token; return result; }
export const monitorApi = {
  login: async (username: string, password: string) => {
    const result = await request<MonitorSession>("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
    csrf = result.csrf_token;
    return result;
  },
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
  dependencies: (params: { days?: number; windowMinutes?: number; bucketMinutes?: number } = {}) => {
    const query = new URLSearchParams({ days: String(params.days ?? 30), window_minutes: String(params.windowMinutes ?? 120), bucket_minutes: String(params.bucketMinutes ?? 5) });
    return request<DependencyHealth>(`/observability/dependencies?${query.toString()}`);
  },
  traces: (limit = 200) => request<{ items: Trace[] }>(`/observability/traces?limit=${limit}`),
  trace: (id: string) => request<TraceDetail>(`/observability/traces/${encodeURIComponent(id)}`),
  traceGroups: (params: { days?: number; limit?: number; offset?: number; focus?: "all" | "errors" | "slow"; query?: string } = {}) => {
    const query = new URLSearchParams();
    query.set("days", String(params.days ?? 30));
    query.set("limit", String(params.limit ?? 24));
    query.set("offset", String(params.offset ?? 0));
    query.set("focus", params.focus ?? "all");
    if (params.query) query.set("query", params.query);
    return request<TraceGroupPage>(`/observability/traces/groups?${query.toString()}`);
  },
  traceGroup: (chainId: string) => request<TraceGroupDetail>(`/observability/traces/chains/${encodeURIComponent(chainId)}`),
  usage: (days: number) => request<{ items: UsageRow[] }>(`/observability/usage?days=${days}`),
  systemUsage: (days: number, includeUsers = false) => request<SystemUsageSnapshot>(`/observability/usage/system?days=${days}&include_users=${includeUsers}`),
  systemUsageUsers: (days: number, limit = 12, offset = 0) => request<SystemUsageUserPage>(`/observability/usage/system/users?days=${days}&limit=${limit}&offset=${offset}`),
  systemUsageDimension: (dimension: SystemUsageDimensionPage["dimension"], days: number, limit = 12, offset = 0) => request<SystemUsageDimensionPage>(`/observability/usage/system/dimensions?dimension=${dimension}&days=${days}&limit=${limit}&offset=${offset}`),
  systemUsageTrend: (windowMinutes = 120, bucketMinutes = 5) => request<SystemUsageSnapshot>(`/observability/usage/system/trend?window_minutes=${windowMinutes}&bucket_minutes=${bucketMinutes}`),
  events: (limit = 300) => request<{ items: TelemetryEvent[] }>(`/observability/events?limit=${limit}`),
  errors: (params: { days?: number; limit?: number; offset?: number; windowMinutes?: number; bucketMinutes?: number } = {}) => {
    const query = new URLSearchParams({ days: String(params.days ?? 30), limit: String(params.limit ?? 100), offset: String(params.offset ?? 0), window_minutes: String(params.windowMinutes ?? 120), bucket_minutes: String(params.bucketMinutes ?? 5) });
    return request<ErrorAnalysis>(`/observability/errors?${query.toString()}`);
  },
  storage: () => request<Record<string, unknown>>("/observability/storage"),
  sandboxOverview: () => request<SandboxOverview>("/observability/sandbox/overview"),
  sandboxLogs: (limit = 80, sinceSeconds = 600) => request<{ items: SandboxLogEntry[]; retention_seconds: number; sampled_at: string }>(`/observability/sandbox/logs?limit=${limit}&since_seconds=${sinceSeconds}`),
  sandboxRuntimes: (limit = 12, offset = 0) => request<SandboxPage<SandboxRuntime>>(`/observability/sandbox/runtimes?limit=${limit}&offset=${offset}`),
  sandboxRuntime: (runtimeId: string) => request<SandboxRuntime>(`/observability/sandbox/runtimes/${encodeURIComponent(runtimeId)}`),
  drainSandboxRuntime: (runtimeId: string) => request<{ id: string; state: string }>(`/observability/sandbox/runtimes/${encodeURIComponent(runtimeId)}/drain`, { method: "POST", body: "{}" }),
  sandboxExecutions: (status?: string, limit = 12, offset = 0) => {
    const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (status) query.set("status_filter", status);
    return request<SandboxPage<SandboxExecution>>(`/observability/sandbox/executions?${query.toString()}`);
  },
  sandboxExecutionEvents: (executionId: string, afterEventId?: string) => request<{ execution_id: string; events: Array<{ event_id: string; seq: number | string; type: string; payload: Record<string, unknown> }> }>(`/observability/sandbox/executions/${encodeURIComponent(executionId)}/events${afterEventId ? `?after_event_id=${encodeURIComponent(afterEventId)}` : ""}`),
  prune: (traceDays?: number, eventDays?: number) => request<Record<string, unknown>>(`/observability/storage/prune${traceDays != null && eventDays != null ? `?trace_days=${traceDays}&event_days=${eventDays}` : ""}`, { method: "POST" }),
  reset: () => request<Record<string, unknown>>("/observability/storage/reset", { method: "POST" }),
};
