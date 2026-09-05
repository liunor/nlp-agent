import type { AuthSession, AuthorizationAuditListResponse, AuthorizationAuditSummary, DeveloperRuntimeHealth, DeveloperSnapshot, LearningBookNavigationItem, LearningBookPage, QuotaAdjustment, QuotaAlert, QuotaArchiveBatch, QuotaBillingRecord, QuotaBillingStatementInput, QuotaBinding, QuotaBucketCandidate, QuotaBucketReplay, QuotaCreditOperation, QuotaCreditOperationInput, QuotaDailyRollup, QuotaGrant, QuotaPolicy, QuotaPolicyExplanation, QuotaPolicyUpdateInput, QuotaPricingRule, QuotaRoleCreditOperationInput, QuotaRoleCreditOperationResult, QuotaSnapshot, QuotaUsageSnapshot, RbacPermission, RbacRole, ReleaseNoteEntry, SessionListResponse, SettingsRuntime, SystemMenu, TeacherAIAnalysisResult, TeacherBookArchiveImportPreview, TeacherBookAssetInput, TeacherBookImportPreview, TeacherBookNavigationItem, TeacherBookPage, TeacherCatalog, TeacherOverview, TeacherAnalysisAnnotations, TeachingGoals, SessionSummary, TurnRecord, UserSettings, UserListResponse, UserProfile, Workspace, WorkspaceMember, ClassroomSummary, JoinRequest, JoinRequestListResponse } from "@/shared/types";
import type { FeedbackCategory, FeedbackDailyState, FeedbackPriority, FeedbackStatus, FeedbackThread, FeedbackThreadList } from "@/shared/types";

const API_ROOT = "/api/v1";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code?: string,
  ) {
    super(message);
  }
}

let csrfToken = "";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    if (!(init.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  if (!response.ok) {
    const problem = await response.json().catch(() => ({})) as { detail?: string; title?: string; code?: string };
    throw new ApiError(problem.detail ?? problem.title ?? `HTTP ${response.status}`, response.status, problem.code);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function ensureAuth(): Promise<AuthSession> {
  const session = await request<AuthSession>("/auth/session");
  csrfToken = session.csrf_token;
  return session;
}

export interface UploadResponse {
  file_name: string;
  url: string;
  media_type: string;
  size_bytes: number;
  width: number;
  height: number;
  sha256: string;
}

export interface SandboxRuntimeProfile {
  id: string;
  runtime: string;
  isolation: string;
  python_version: string;
  kernel_version: string;
  pytorch_version: string;
  pytorch_device: string;
}

export interface SandboxRuntimeUsage {
  cpu_percent: number | null;
  memory_percent: number | null;
  sampled_at: string | null;
}

export async function uploadAttachment(
  sessionId: string,
  file: File,
): Promise<UploadResponse> {
  const form = new FormData();
  form.append("session_id", sessionId);
  form.append("file", file);
  return request<UploadResponse>("/uploads", {
    method: "POST",
    body: form,
  });
}

export const api = {
  login: async (username: string, password: string) => {
    const session = await request<AuthSession>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    csrfToken = session.csrf_token;
    return session;
  },
  logout: async () => {
    await request<void>("/auth/session", { method: "DELETE" });
    csrfToken = "";
  },
  getAuthSession: ensureAuth,
  ensureSandboxLease: () => request<{
    phase: number;
      runtime_available: boolean;
      environment: { id: string; status: string; generation: number; profile: string } | null;
      lease: { id: string; state: string; generation: number; expires_at: string } | null;
      runtime: { id: string; generation: number; ticket: string | null } | { kind: "inmemory"; ticket: null } | null;
      runtime_profile: SandboxRuntimeProfile;
      pool_status?: string;
    }>("/sandbox/lease", { method: "POST" }),
  executeSandbox: (source: string, ticket: string | null) => request<{ status?: string; stdout: string; stderr: string; ticket?: string; execution_id?: string; execution_metrics?: { duration_ms: number; output_bytes: number }; artifacts?: Array<{ id: string; mime_type: string }> }>("/sandbox/execute", { method: "POST", body: JSON.stringify({ source, ticket }) }),
  getSandboxUsage: (ticket: string | null) => request<SandboxRuntimeUsage>("/sandbox/usage", { method: "POST", body: JSON.stringify({ ticket }) }),
  restartSandbox: (ticket: string | null) => request<{ status: string; ticket?: string | null }>("/sandbox/restart", { method: "POST", body: JSON.stringify({ ticket }) }),
  replaySandboxEvents: (executionId: string, afterEventId?: string) => request<{ execution_id: string; events: Array<{ event_id: string; seq: number | string; type: string; payload: { text?: string } }> }>(`/sandbox/executions/${encodeURIComponent(executionId)}/events${afterEventId ? `?after_event_id=${encodeURIComponent(afterEventId)}` : ""}`),
  getSandboxArtifactUrl: (artifactId: string) => request<{ url: string }>(`/sandbox/artifacts/${encodeURIComponent(artifactId)}/access`),
  createWsTicket: () => request<{ ticket: string; expires_in: number }>("/auth/ws-ticket", { method: "POST", body: "{}" }),
  listSessions: (params?: { limit?: number; offset?: number }) => {
    const query = new URLSearchParams();
    if (params?.limit != null) query.set("limit", String(params.limit));
    if (params?.offset != null) query.set("offset", String(params.offset));
    const suffix = query.size > 0 ? `?${query.toString()}` : "";
    return request<SessionListResponse>(`/sessions${suffix}`);
  },
  getUsage: (days = 30, workspaceId?: string, granularity: "day" | "week" = "day") => request<QuotaUsageSnapshot>(`/usage/me?days=${encodeURIComponent(String(days))}&granularity=${granularity}${workspaceId ? `&workspace_id=${encodeURIComponent(workspaceId)}` : ""}`),
  getQuota: (workspaceId?: string) => request<{ quota: QuotaSnapshot; policy: QuotaPolicyExplanation | null }>(`/quota/me${workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ""}`),
  createSession: (workspaceId = "default") =>
    request<SessionSummary>("/sessions", {
      method: "POST",
      body: JSON.stringify({ workspace_id: workspaceId }),
    }),
  deleteSession: (sessionId: string) =>
    request<void>(`/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }),
  renameSession: (sessionId: string, title: string) =>
    request<{ session_id: string; title: string }>(`/sessions/${encodeURIComponent(sessionId)}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  listTurns: (sessionId: string) =>
    request<{ items: TurnRecord[] }>(`/sessions/${encodeURIComponent(sessionId)}/turns?limit=500`),
  cancelTurn: (turnId: string) =>
    request<TurnRecord>(`/chat/turns/${encodeURIComponent(turnId)}/cancel`, { method: "POST" }),
  getSettings: () => request<{ preferences: { settings?: Partial<UserSettings> }; runtime: SettingsRuntime }>("/settings"),
  updateSettings: (settings: Partial<UserSettings>) =>
    request<{ settings: Partial<UserSettings> }>("/settings", {
      method: "PATCH",
      body: JSON.stringify(settings),
    }),
  submitFeedback: (body: string, category?: FeedbackCategory) => request<{ thread_id: string; remaining: number; daily_limit: number }>("/feedback", { method: "POST", body: JSON.stringify({ body, category }) }),
  getFeedbackDailyState: () => request<FeedbackDailyState>("/feedback/daily-state"),
  markOwnFeedbackRead: () => request<{ ok: boolean; updated: boolean }>("/feedback/read", { method: "POST" }),
  getOwnFeedback: (params?: { limit?: number; offset?: number }) => {
    const query = new URLSearchParams();
    if (params?.limit != null) query.set("limit", String(params.limit));
    if (params?.offset != null) query.set("offset", String(params.offset));
    const suffix = query.size > 0 ? `?${query.toString()}` : "";
    return request<FeedbackThread & { thread_id: string | null }>(`/feedback${suffix}`);
  },
  listFeedback: (params?: { limit?: number; offset?: number; q?: string; status?: FeedbackStatus; category?: FeedbackCategory; priority?: FeedbackPriority; sort?: "latest" | "oldest" | "unread" }) => {
    const query = new URLSearchParams();
    if (params?.limit != null) query.set("limit", String(params.limit));
    if (params?.offset != null) query.set("offset", String(params.offset));
    if (params?.q) query.set("q", params.q);
    if (params?.status) query.set("status", params.status);
    if (params?.category) query.set("category", params.category);
    if (params?.priority) query.set("priority", params.priority);
    if (params?.sort) query.set("sort", params.sort);
    const suffix = query.size > 0 ? `?${query.toString()}` : "";
    return request<FeedbackThreadList>(`/developer/feedback${suffix}`);
  },
  getFeedback: (threadId: string, params?: { limit?: number; offset?: number }) => {
    const query = new URLSearchParams();
    if (params?.limit != null) query.set("limit", String(params.limit));
    if (params?.offset != null) query.set("offset", String(params.offset));
    const suffix = query.size > 0 ? `?${query.toString()}` : "";
    return request<FeedbackThread>(`/developer/feedback/${encodeURIComponent(threadId)}${suffix}`);
  },
  markFeedbackRead: (threadId: string, messageId: string) => request<{ ok: boolean }>(`/developer/feedback/${encodeURIComponent(threadId)}/read`, { method: "POST", body: JSON.stringify({ read_through_message_id: messageId }) }),
  markFeedbackThreadsRead: (threadIds: string[]) => request<{ ok: boolean; updated: number }>("/developer/feedback/bulk-read", { method: "POST", body: JSON.stringify({ thread_ids: threadIds }) }),
  updateFeedback: (threadId: string, patch: { status?: FeedbackStatus; category?: FeedbackCategory; priority?: FeedbackPriority }) => request<FeedbackThread>(`/developer/feedback/${encodeURIComponent(threadId)}`, { method: "PATCH", body: JSON.stringify(patch) }),
  replyFeedback: (threadId: string, body: string) => request<{ thread_id: string; message: FeedbackThread["messages"][number] }>(`/developer/feedback/${encodeURIComponent(threadId)}/reply`, { method: "POST", body: JSON.stringify({ body }) }),
  deleteFeedback: (threadId: string) => request<void>(`/developer/feedback/${encodeURIComponent(threadId)}`, { method: "DELETE" }),
  deleteFeedbackThreads: (threadIds: string[]) => request<{ ok: boolean; deleted: number }>("/developer/feedback/bulk-delete", { method: "POST", body: JSON.stringify({ thread_ids: threadIds }) }),
  getDeveloperSnapshot: () => request<DeveloperSnapshot>("/developer/snapshot"),
  getDeveloperHealth: () => request<DeveloperRuntimeHealth>("/developer/health"),
  listQuotaPolicies: (code?: string) => request<{ items: QuotaPolicy[] }>(`/developer/quota/policies${code ? `?code=${encodeURIComponent(code)}` : ""}`),
  listQuotaPricingRules: (pricingKey?: string) => request<{ items: QuotaPricingRule[] }>(`/developer/quota/pricing-rules${pricingKey ? `?pricing_key=${encodeURIComponent(pricingKey)}` : ""}`),
  getQuotaPricingRule: (pricingRuleId: string) => request<QuotaPricingRule>(`/developer/quota/pricing-rules/${encodeURIComponent(pricingRuleId)}`),
  createQuotaPricingRule: (input: Omit<QuotaPricingRule, "pricing_rule_id" | "status" | "created_by" | "created_at">) => request<QuotaPricingRule>("/developer/quota/pricing-rules", { method: "POST", body: JSON.stringify(input) }),
  retireQuotaPricingRule: (pricingRuleId: string) => request<QuotaPricingRule>(`/developer/quota/pricing-rules/${encodeURIComponent(pricingRuleId)}`, { method: "DELETE" }),
  getQuotaPolicy: (policyId: string) => request<QuotaPolicy>(`/developer/quota/policies/${encodeURIComponent(policyId)}`),
  createQuotaPolicy: (input: Omit<QuotaPolicy, "policy_id" | "created_by" | "created_at" | "updated_at">) => request<QuotaPolicy>("/developer/quota/policies", { method: "POST", body: JSON.stringify(input) }),
  updateQuotaPolicy: (policyId: string, input: QuotaPolicyUpdateInput) => request<QuotaPolicy>(`/developer/quota/policies/${encodeURIComponent(policyId)}`, { method: "PATCH", body: JSON.stringify(input) }),
  archiveQuotaPolicy: (policyId: string) => request<QuotaPolicy>(`/developer/quota/policies/${encodeURIComponent(policyId)}/archive`, { method: "POST", body: "{}" }),
  publishQuotaPolicy: (policyId: string) => request<QuotaPolicy>(`/developer/quota/policies/${encodeURIComponent(policyId)}/publish`, { method: "POST", body: "{}" }),
  listQuotaBindings: () => request<{ items: QuotaBinding[] }>("/developer/quota/bindings"),
  getQuotaBinding: (bindingId: string) => request<QuotaBinding>(`/developer/quota/bindings/${encodeURIComponent(bindingId)}`),
  bindQuotaPolicy: (input: Omit<QuotaBinding, "binding_id" | "policy_code" | "policy_version" | "status">) => request<QuotaBinding>("/developer/quota/bindings", { method: "POST", body: JSON.stringify(input) }),
  retireQuotaBinding: (bindingId: string) => request<QuotaBinding>(`/developer/quota/bindings/${encodeURIComponent(bindingId)}`, { method: "DELETE" }),
  listQuotaGrants: (ownerType?: string, ownerId?: string) => request<{ items: QuotaGrant[] }>(`/developer/quota/grants${ownerType || ownerId ? `?${new URLSearchParams({ ...(ownerType ? { owner_type: ownerType } : {}), ...(ownerId ? { owner_id: ownerId } : {}) })}` : ""}`),
  createQuotaGrant: (input: Omit<QuotaGrant, "grant_id" | "created_by" | "created_at" | "revoked_at" | "revoked_by" | "revocation_idempotency_key" | "status">) => request<QuotaGrant>("/developer/quota/grants", { method: "POST", body: JSON.stringify(input) }),
  getQuotaGrant: (grantId: string) => request<QuotaGrant>(`/developer/quota/grants/${encodeURIComponent(grantId)}`),
  revokeQuotaGrant: (grantId: string, idempotency_key: string) => request<QuotaGrant>(`/developer/quota/grants/${encodeURIComponent(grantId)}`, { method: "DELETE", body: JSON.stringify({ idempotency_key }) }),
  listQuotaAdjustments: (ownerType?: string, ownerId?: string) => request<{ items: QuotaAdjustment[] }>(`/developer/quota/adjustments${ownerType || ownerId ? `?${new URLSearchParams({ ...(ownerType ? { owner_type: ownerType } : {}), ...(ownerId ? { owner_id: ownerId } : {}) })}` : ""}`),
  getQuotaAdjustment: (adjustmentId: string) => request<QuotaAdjustment>(`/developer/quota/adjustments/${encodeURIComponent(adjustmentId)}`),
  listQuotaDailyRollups: (start: string, end: string, filters: { userId?: string; workspaceId?: string } = {}) => {
    const query = new URLSearchParams({ start, end });
    if (filters.userId) query.set("user_id", filters.userId);
    if (filters.workspaceId) query.set("workspace_id", filters.workspaceId);
    return request<{ items: QuotaDailyRollup[] }>(`/developer/quota/daily-rollups?${query.toString()}`);
  },
  listQuotaBilling: (status?: string, limit = 100) => request<{ items: QuotaBillingRecord[] }>(`/developer/quota/billing?limit=${limit}${status ? `&status=${encodeURIComponent(status)}` : ""}`),
  reconcileQuotaBilling: (statement: QuotaBillingStatementInput) => request<{ total: number; matched: number; discrepancies: number; unmatched: number; items: QuotaBillingRecord[] }>("/developer/quota/billing/reconcile", { method: "POST", body: JSON.stringify({ statements: [statement] }) }),
  repairQuotaBilling: (billingId: string, reason: string, idempotencyKey: string) => request<QuotaBillingRecord>(`/developer/quota/billing/${encodeURIComponent(billingId)}/repair`, { method: "POST", body: JSON.stringify({ reason, idempotency_key: idempotencyKey }) }),
  giftQuotaCredits: (input: QuotaCreditOperationInput) => request<QuotaCreditOperation>("/developer/quota/credits/gift", { method: "POST", body: JSON.stringify(input) }),
  giftQuotaRoleCredits: (input: QuotaRoleCreditOperationInput) => request<QuotaRoleCreditOperationResult>("/developer/quota/credits/gift-role", { method: "POST", body: JSON.stringify(input) }),
  resetQuotaCredits: (input: QuotaCreditOperationInput) => request<QuotaCreditOperation>("/developer/quota/credits/reset", { method: "POST", body: JSON.stringify(input) }),
  listQuotaCreditOperations: (limit = 100) => request<{ items: QuotaCreditOperation[] }>(`/developer/quota/credits?limit=${limit}`),
  listQuotaAlerts: (status?: string, limit = 100) => request<{ items: QuotaAlert[] }>(`/developer/quota/alerts?limit=${limit}${status ? `&status=${encodeURIComponent(status)}` : ""}`),
  updateQuotaAlert: (alertId: string, status: "acknowledged" | "resolved", reason: string) => request<QuotaAlert>(`/developer/quota/alerts/${encodeURIComponent(alertId)}`, { method: "PATCH", body: JSON.stringify({ status, reason }) }),
  archiveQuotaUsage: (before: string, batchSize = 10_000) => request<{ batch_id: string | null; archived_events: number; cutoff_at: string; deleted_events: number }>("/developer/quota/archive", { method: "POST", body: JSON.stringify({ before, batch_size: batchSize }) }),
  purgeQuotaUsage: (before: string, batchSize = 10_000) => request<{ purged_events: number; deleted_events: number; cutoff_at: string }>("/developer/quota/archive/purge", { method: "POST", body: JSON.stringify({ before, batch_size: batchSize }) }),
  listQuotaArchiveBatches: (limit = 100) => request<{ items: QuotaArchiveBatch[] }>(`/developer/quota/archive?limit=${limit}`),
  listQuotaBuckets: (ownerType?: string, ownerId?: string, limit = 200) => {
    const query = new URLSearchParams({ limit: String(limit) });
    if (ownerType) query.set("owner_type", ownerType);
    if (ownerId) query.set("owner_id", ownerId);
    return request<{ items: QuotaBucketCandidate[] }>(`/developer/quota/buckets?${query.toString()}`);
  },
  replayQuotaBucket: (bucketId: string) => request<QuotaBucketReplay>(`/developer/quota/buckets/${encodeURIComponent(bucketId)}/replay`),
  repairQuotaBucket: (bucketId: string, reason: string, idempotencyKey: string) => request<QuotaBucketReplay>(`/developer/quota/buckets/${encodeURIComponent(bucketId)}/repair`, { method: "POST", body: JSON.stringify({ reason, idempotency_key: idempotencyKey }) }),
  createQuotaAdjustment: (input: Omit<QuotaAdjustment, "adjustment_id" | "actor_user_id" | "created_at">) => request<QuotaAdjustment>("/developer/quota/adjustments", { method: "POST", body: JSON.stringify(input) }),
  updateToolPolicies: (policies: Record<string, unknown>) =>
    request<Record<string, unknown>>("/developer/tools/policies", { method: "PUT", body: JSON.stringify({ policies }) }),
  updateCustomTools: (custom: Record<string, unknown>) => request<{ restart_required: boolean; reason: string }>("/developer/tools/custom", { method: "PUT", body: JSON.stringify({ custom }) }),
  saveMcp: (name: string, config: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/developer/mcp/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify({ config }) }),
  testMcp: (name: string, config: Record<string, unknown>) =>
    request<{ ok: boolean; server: string; tools: string[] }>(`/developer/mcp/${encodeURIComponent(name)}/test`, { method: "POST", body: JSON.stringify({ config }) }),
  deleteMcp: (name: string) => request<void>(`/developer/mcp/${encodeURIComponent(name)}`, { method: "DELETE" }),
  getSkill: (name: string) => request<{ name: string; content: string }>(`/developer/skills/${encodeURIComponent(name)}`),
  saveSkill: (name: string, content: string) => request<Record<string, unknown>>(`/developer/skills/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify({ content }) }),
  deleteSkill: (name: string) => request<void>(`/developer/skills/${encodeURIComponent(name)}`, { method: "DELETE" }),
  saveWorkerProfile: (name: string, profile: Record<string, unknown>) => request<Record<string, unknown>>(`/developer/worker-profiles/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify({ profile }) }),
  deleteWorkerProfile: (name: string) => request<void>(`/developer/worker-profiles/${encodeURIComponent(name)}`, { method: "DELETE" }),
  saveModelProvider: (name: string, config: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/developer/models/providers/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify({ config }) }),
  saveModelPreset: (name: string, config: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/developer/models/presets/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify({ config }) }),
  saveModelRoute: (name: string, config: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/developer/models/routes/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify({ config }) }),
  saveModelProfile: (name: string, config: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/developer/models/profiles/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify({ config }) }),
  listReleaseNotes: () => request<{ items: ReleaseNoteEntry[] }>("/developer/release-notes"),
  createReleaseNote: (note: Omit<ReleaseNoteEntry, "id">) =>
    request<ReleaseNoteEntry>("/developer/release-notes", { method: "POST", body: JSON.stringify(note) }),
  updateReleaseNote: (noteId: string, note: Omit<ReleaseNoteEntry, "id">) =>
    request<ReleaseNoteEntry>(`/developer/release-notes/${encodeURIComponent(noteId)}`, { method: "PUT", body: JSON.stringify(note) }),
  deleteReleaseNote: (noteId: string) => request<void>(`/developer/release-notes/${encodeURIComponent(noteId)}`, { method: "DELETE" }),
  listPublishedReleaseNotes: () => request<{ items: ReleaseNoteEntry[] }>("/learning/release-notes"),
  getTeacherOverview: (workspaceId = "default", days = 30) =>
    request<TeacherOverview>(`/teacher/overview?workspace_id=${encodeURIComponent(workspaceId)}&days=${days}`),
  generateTeacherAIAnalysis: (workspaceId: string, body: { course_id: string; content_scope: string; start_date?: string; end_date?: string; period_days?: number; force_refresh?: boolean }) =>
    request<TeacherAIAnalysisResult>("/teacher/reports/ai-analysis", { method: "POST", body: JSON.stringify({ workspace_id: workspaceId, ...body }) }),
  updateTeachingGoals: (workspaceId: string, goals: Omit<TeachingGoals, "workspace_id">) =>
    request<{ goals: TeachingGoals; revision: number; updated_at: string }>(`/teacher/goals/${encodeURIComponent(workspaceId)}`, {
      method: "PUT",
      body: JSON.stringify(goals),
    }),
  updateTeacherAnalysisAnnotations: (workspaceId: string, annotations: Omit<TeacherAnalysisAnnotations, "workspace_id">) =>
    request<{ annotations: TeacherAnalysisAnnotations; revision: number; updated_at: string }>(`/teacher/analysis-annotations/${encodeURIComponent(workspaceId)}`, {
      method: "PUT",
      body: JSON.stringify(annotations),
    }),
  getTeacherCatalog: (workspaceId = "default") => request<{ catalog: TeacherCatalog }>(`/teacher/catalog/${encodeURIComponent(workspaceId)}`),
  updateTeacherCatalog: (workspaceId: string, catalog: Omit<TeacherCatalog, "workspace_id">) => request<{ catalog: TeacherCatalog }>(`/teacher/catalog/${encodeURIComponent(workspaceId)}`, { method: "PUT", body: JSON.stringify(catalog) }),
  saveExerciseBlueprint: (workspaceId: string, blueprint: TeacherCatalog["exercise_blueprints"][number]) => request<{ catalog: TeacherCatalog }>(`/teacher/catalog/${encodeURIComponent(workspaceId)}/exercise-blueprints/${encodeURIComponent(blueprint.id)}`, { method: "PUT", body: JSON.stringify(blueprint) }),
  saveReviewBlueprint: (workspaceId: string, blueprint: TeacherCatalog["review_blueprints"][number]) => request<{ catalog: TeacherCatalog }>(`/teacher/catalog/${encodeURIComponent(workspaceId)}/review-blueprints/${encodeURIComponent(blueprint.id)}`, { method: "PUT", body: JSON.stringify(blueprint) }),
  saveGuidedBlueprint: (workspaceId: string, blueprint: TeacherCatalog["guided_blueprints"][number]) => request<{ catalog: TeacherCatalog }>(`/teacher/catalog/${encodeURIComponent(workspaceId)}/guided-blueprints/${encodeURIComponent(blueprint.id)}`, { method: "PUT", body: JSON.stringify(blueprint) }),
  deleteBlueprint: (workspaceId: string, kind: "exercise" | "review", blueprintId: string) => request<void>(`/teacher/catalog/${encodeURIComponent(workspaceId)}/${kind}-blueprints/${encodeURIComponent(blueprintId)}`, { method: "DELETE" }),
  getLearningCatalog: (workspaceId = "default") => request<{ catalog: TeacherCatalog }>(`/learning/catalog/${encodeURIComponent(workspaceId)}`),
  getTeacherBookNavigation: (workspaceId = "default") => request<{ workspace_id: string; items: TeacherBookNavigationItem[] }>(`/teacher/book/${encodeURIComponent(workspaceId)}/navigation`),
  getTeacherBookPage: (workspaceId: string, knowledgePointId: string) => request<{ page: TeacherBookPage }>(`/teacher/book/${encodeURIComponent(workspaceId)}/pages/${encodeURIComponent(knowledgePointId)}`),
  updateTeacherBookPage: (workspaceId: string, knowledgePointId: string, content_markdown: string, expected_revision: number, assets: TeacherBookAssetInput[] = []) => request<{ page: TeacherBookPage; warnings: string[] }>(`/teacher/book/${encodeURIComponent(workspaceId)}/pages/${encodeURIComponent(knowledgePointId)}`, { method: "PUT", body: JSON.stringify({ content_markdown, expected_revision, assets }) }),
  publishTeacherBookPage: (workspaceId: string, knowledgePointId: string, expected_revision: number) => request<{ page: TeacherBookPage }>(`/teacher/book/${encodeURIComponent(workspaceId)}/pages/${encodeURIComponent(knowledgePointId)}/publish`, { method: "POST", body: JSON.stringify({ expected_revision }) }),
  previewTeacherBookImport: (workspaceId: string, file_name: string, content_markdown: string) => request<TeacherBookImportPreview>(`/teacher/book/${encodeURIComponent(workspaceId)}/imports/preview`, { method: "POST", body: JSON.stringify({ file_name, content_markdown }) }),
  applyTeacherBookImport: (workspaceId: string, knowledgePointId: string, file_name: string, content_markdown: string, expected_revision: number, assets: TeacherBookAssetInput[] = []) => request<{ page: TeacherBookPage }>(`/teacher/book/${encodeURIComponent(workspaceId)}/imports/apply`, { method: "POST", body: JSON.stringify({ knowledge_point_id: knowledgePointId, file_name, content_markdown, expected_revision, assets }) }),
  previewTeacherBookArchiveImport: (workspaceId: string, file_name: string, archive_base64: string) => request<TeacherBookArchiveImportPreview>(`/teacher/book/${encodeURIComponent(workspaceId)}/imports/archive/preview`, { method: "POST", body: JSON.stringify({ file_name, archive_base64 }) }),
  applyTeacherBookArchiveImport: (workspaceId: string, file_name: string, archive_base64: string, expected_revisions: Record<string, number>) => request<{ pages: TeacherBookPage[]; asset_paths: string[]; applied_count: number }>(`/teacher/book/${encodeURIComponent(workspaceId)}/imports/archive/apply`, { method: "POST", body: JSON.stringify({ file_name, archive_base64, expected_revisions }) }),
  getLearningBookNavigation: (workspaceId = "default") => request<{ workspace_id: string; items: LearningBookNavigationItem[] }>(`/learning/book/${encodeURIComponent(workspaceId)}/navigation`),
  getLearningBookPage: (workspaceId: string, knowledgePointId: string) => request<{ page: LearningBookPage }>(`/learning/book/${encodeURIComponent(workspaceId)}/pages/${encodeURIComponent(knowledgePointId)}`),
  getTeacherResource: (resource: "courses" | "prompts" | "reports", workspaceId = "default") =>
    request<{ items: unknown[]; status: string }>(`/teacher/${resource}?workspace_id=${encodeURIComponent(workspaceId)}`),

  // ---- Admin module (用户 / 工作区 / 班级加入申请) ----
  listUsers: (offset = 0, limit = 12, status?: string, keyword?: string, includeDeleted = false) =>
    request<UserListResponse>(
      `/users?offset=${offset}&limit=${limit}${status ? `&status=${encodeURIComponent(status)}` : ""}${keyword ? `&keyword=${encodeURIComponent(keyword)}` : ""}${includeDeleted ? "&include_deleted=true" : ""}`,
    ),
  createUser: (input: { username: string; display_name: string; password: string; role_codes?: string[] }) =>
    request<UserListResponse["users"][number]>("/users", { method: "POST", body: JSON.stringify(input) }),
  updateUser: (userId: string, input: { display_name?: string; status?: "active" | "disabled" | "locked" }) =>
    request<UserListResponse["users"][number]>(`/users/${encodeURIComponent(userId)}`, { method: "PATCH", body: JSON.stringify(input) }),
  disableUser: (userId: string) =>
    request<void>(`/users/${encodeURIComponent(userId)}/disable`, { method: "POST" }),
  enableUser: (userId: string) =>
    request<void>(`/users/${encodeURIComponent(userId)}/enable`, { method: "POST" }),
  restoreUser: (userId: string) =>
    request<UserListResponse["users"][number]>(`/users/${encodeURIComponent(userId)}/restore`, { method: "POST", body: "{}" }),
  deleteUser: (userId: string) =>
    request<void>(`/users/${encodeURIComponent(userId)}`, { method: "DELETE" }),
  revokeUserSessions: (userId: string) =>
    request<void>(`/users/${encodeURIComponent(userId)}/sessions/revoke`, { method: "POST", body: "{}" }),
  resetUserPassword: (userId: string, new_password: string) =>
    request<void>(`/users/${encodeURIComponent(userId)}/password`, { method: "POST", body: JSON.stringify({ new_password }) }),
  getUserRoles: (userId: string) => request<{ user_id: string; role_codes: string[] }>(`/users/${encodeURIComponent(userId)}/roles`),
  replaceUserRoles: (userId: string, role_codes: string[]) =>
    request<{ user_id: string; role_codes: string[] }>(`/users/${encodeURIComponent(userId)}/roles`, { method: "PUT", body: JSON.stringify({ role_codes }) }),
  listRoles: () => request<{ items: RbacRole[] }>("/roles"),
  listPermissions: () => request<{ items: RbacPermission[] }>("/permissions"),
  listRolePermissions: (roleCode: string) => request<{ role_code: string; permissions: Record<string, string[]> }>(`/system/roles/${encodeURIComponent(roleCode)}/permissions`),
  replaceRolePermissions: (roleCode: string, permission_codes: string[], scopes: Record<string, string[]>) =>
    request<void>(`/system/roles/${encodeURIComponent(roleCode)}/permissions`, { method: "PUT", body: JSON.stringify({ permission_codes, scopes }) }),
  listMenus: () => request<{ items: SystemMenu[] }>("/system/menus"),
  listVisibleMenus: () => request<{ items: SystemMenu[] }>("/system/menus/visible"),
  replaceRoleMenus: (roleCode: string, menu_ids: string[]) =>
    request<void>(`/system/roles/${encodeURIComponent(roleCode)}/menus`, { method: "PUT", body: JSON.stringify({ menu_ids }) }),
  listRoleMenus: (roleCode: string) => request<{ role_code: string; menu_ids: string[] }>(`/system/roles/${encodeURIComponent(roleCode)}/menus`),
  listAuthorizationAudit: (params?: { limit?: number; offset?: number; actorUserId?: string; decision?: string; reasonCode?: string }) => {
    const query = new URLSearchParams();
    if (params?.limit != null) query.set("limit", String(params.limit));
    if (params?.offset != null) query.set("offset", String(params.offset));
    if (params?.actorUserId) query.set("actor_user_id", params.actorUserId);
    if (params?.decision) query.set("decision", params.decision);
    if (params?.reasonCode) query.set("reason_code", params.reasonCode);
    const suffix = query.size > 0 ? `?${query.toString()}` : "";
    return request<AuthorizationAuditListResponse>(`/audit/authorization${suffix}`);
  },
  getAuthorizationAuditStats: (days = 30) => request<AuthorizationAuditSummary>(`/audit/authorization/stats?days=${days}`),
  listWorkspaces: () => request<{ workspaces: Workspace[]; total: number }>("/workspaces"),
  listWorkspaceMembers: (workspaceId: string) =>
    request<WorkspaceMember[]>(`/workspaces/${encodeURIComponent(workspaceId)}/members`),
  listClassrooms: () => request<{ items: ClassroomSummary[] }>("/classrooms"),
  listJoinRequests: (classroomId: string) =>
    request<JoinRequestListResponse>(`/classrooms/${encodeURIComponent(classroomId)}/join-requests`),
  approveJoinRequest: (classroomId: string, requestId: string) =>
    request<JoinRequest>(
      `/classrooms/${encodeURIComponent(classroomId)}/join-requests/${encodeURIComponent(requestId)}/approve`,
      { method: "POST", body: "{}" },
    ),
  rejectJoinRequest: (classroomId: string, requestId: string) =>
    request<JoinRequest>(
      `/classrooms/${encodeURIComponent(classroomId)}/join-requests/${encodeURIComponent(requestId)}/reject`,
      { method: "POST" },
    ),

  // ---------------------------------------------------------------------------
  // Registration (public)
  // ---------------------------------------------------------------------------
  getCaptcha: () =>
    request<{ captcha_id: string; image: string }>("/auth/captcha"),
  register: (data: { phone_number: string; sms_code: string; password: string; display_name?: string; captcha_id: string; captcha_code: string }) =>
    request<UserProfile>("/auth/register", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  sendSmsCode: (phoneNumber: string, captchaId: string, captchaCode: string) =>
    request<{ message: string }>("/auth/sms/send", {
      method: "POST",
      body: JSON.stringify({ phone_number: phoneNumber, captcha_id: captchaId, captcha_code: captchaCode }),
    }),

  // ---------------------------------------------------------------------------
  // Self-service profile (当前用户)
  // ---------------------------------------------------------------------------
  getCurrentUser: () => request<UserProfile>("/users/me"),
  updateProfile: (data: { display_name: string }) =>
    request<UserProfile>("/users/me", {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  changePassword: (data: { current_password: string; new_password: string }) =>
    request<void>("/users/me/password", {
      method: "POST",
      body: JSON.stringify(data),
    }),
};
