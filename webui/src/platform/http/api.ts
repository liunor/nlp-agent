import type {
  AuthSession,
  DeveloperSnapshot,
  SettingsRuntime,
  TeacherCatalog,
  TeacherOverview,
  TeachingGoals,
  SessionSummary,
  TurnRecord,
  UserSettings,
  MeResponse,
  UserListResponse,
  UserProfile,
  WorkspaceListResponse,
  Workspace,
  WorkspaceMember,
  AgentSessionListResponse,
  AgentSession,
  ActiveSessionListResponse,
} from "@/shared/types";

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
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
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
  listSessions: () => request<{ items: SessionSummary[] }>("/sessions"),
  createSession: (workspaceId = "default") =>
    request<SessionSummary>("/sessions", {
      method: "POST",
      body: JSON.stringify({ workspace_id: workspaceId }),
    }),
  deleteSession: (sessionId: string) =>
    request<void>(`/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }),
  listTurns: (sessionId: string) =>
    request<{ items: TurnRecord[] }>(`/sessions/${encodeURIComponent(sessionId)}/turns?limit=500`),
  cancelTurn: (turnId: string) =>
    request<TurnRecord>(`/chat/turns/${encodeURIComponent(turnId)}/cancel`, { method: "POST" }),
  getSettings: () => request<{ preferences: { settings?: Partial<UserSettings> }; runtime: SettingsRuntime }>("/settings"),
  updateSettings: (settings: Partial<UserSettings>) =>
    request<{ settings: UserSettings }>("/settings", {
      method: "PATCH",
      body: JSON.stringify(settings),
    }),
  getDeveloperSnapshot: () => request<DeveloperSnapshot>("/developer/snapshot"),
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
  getTeacherOverview: (workspaceId = "default", days = 30) =>
    request<TeacherOverview>(`/teacher/overview?workspace_id=${encodeURIComponent(workspaceId)}&days=${days}`),
  updateTeachingGoals: (workspaceId: string, goals: Omit<TeachingGoals, "workspace_id">) =>
    request<{ goals: TeachingGoals; revision: number; updated_at: string }>(`/teacher/goals/${encodeURIComponent(workspaceId)}`, {
      method: "PUT",
      body: JSON.stringify(goals),
    }),
  getTeacherCatalog: (workspaceId = "default") => request<{ catalog: TeacherCatalog }>(`/teacher/catalog/${encodeURIComponent(workspaceId)}`),
  updateTeacherCatalog: (workspaceId: string, catalog: Omit<TeacherCatalog, "workspace_id">) => request<{ catalog: TeacherCatalog }>(`/teacher/catalog/${encodeURIComponent(workspaceId)}`, { method: "PUT", body: JSON.stringify(catalog) }),
  saveExerciseBlueprint: (workspaceId: string, blueprint: TeacherCatalog["exercise_blueprints"][number]) => request<{ catalog: TeacherCatalog }>(`/teacher/catalog/${encodeURIComponent(workspaceId)}/exercise-blueprints/${encodeURIComponent(blueprint.id)}`, { method: "PUT", body: JSON.stringify(blueprint) }),
  saveReviewBlueprint: (workspaceId: string, blueprint: TeacherCatalog["review_blueprints"][number]) => request<{ catalog: TeacherCatalog }>(`/teacher/catalog/${encodeURIComponent(workspaceId)}/review-blueprints/${encodeURIComponent(blueprint.id)}`, { method: "PUT", body: JSON.stringify(blueprint) }),
  saveGuidedBlueprint: (workspaceId: string, blueprint: TeacherCatalog["guided_blueprints"][number]) => request<{ catalog: TeacherCatalog }>(`/teacher/catalog/${encodeURIComponent(workspaceId)}/guided-blueprints/${encodeURIComponent(blueprint.id)}`, { method: "PUT", body: JSON.stringify(blueprint) }),
  deleteBlueprint: (workspaceId: string, kind: "exercise" | "review", blueprintId: string) => request<void>(`/teacher/catalog/${encodeURIComponent(workspaceId)}/${kind}-blueprints/${encodeURIComponent(blueprintId)}`, { method: "DELETE" }),
  getLearningCatalog: (workspaceId = "default") => request<{ catalog: TeacherCatalog }>(`/learning/catalog/${encodeURIComponent(workspaceId)}`),
  getTeacherResource: (resource: "courses" | "prompts" | "reports", workspaceId = "default") =>
    request<{ items: unknown[]; status: string }>(`/teacher/${resource}?workspace_id=${encodeURIComponent(workspaceId)}`),

  // ---------------------------------------------------------------------------
  // User management
  // ---------------------------------------------------------------------------
  getMe: () => request<MeResponse>("/auth/me"),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<void>("/auth/password", {
      method: "POST",
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),
  listUsers: (offset = 0, limit = 100, status?: string) => {
    const params = new URLSearchParams({ offset: String(offset), limit: String(limit) });
    if (status) params.set("status", status);
    return request<UserListResponse>(`/users?${params}`);
  },
  getUser: (userId: string) => request<UserProfile>(`/users/${encodeURIComponent(userId)}`),
  updateUser: (userId: string, data: { display_name?: string; status?: string }) =>
    request<UserProfile>(`/users/${encodeURIComponent(userId)}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  disableUser: (userId: string) =>
    request<void>(`/users/${encodeURIComponent(userId)}/disable`, { method: "POST" }),
  enableUser: (userId: string) =>
    request<void>(`/users/${encodeURIComponent(userId)}/enable`, { method: "POST" }),

  // ---------------------------------------------------------------------------
  // Workspace management
  // ---------------------------------------------------------------------------
  listWorkspaces: () => request<WorkspaceListResponse>("/workspaces"),
  createWorkspace: (data: { name: string; slug?: string; type?: string }) =>
    request<Workspace>("/workspaces", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  getWorkspace: (workspaceId: string) =>
    request<Workspace>(`/workspaces/${encodeURIComponent(workspaceId)}`),
  listWorkspaceMembers: (workspaceId: string) =>
    request<WorkspaceMember[]>(`/workspaces/${encodeURIComponent(workspaceId)}/members`),
  addWorkspaceMember: (workspaceId: string, userId: string, memberType = "member") =>
    request<WorkspaceMember>(`/workspaces/${encodeURIComponent(workspaceId)}/members`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId, member_type: memberType }),
    }),
  removeWorkspaceMember: (workspaceId: string, userId: string) =>
    request<void>(`/workspaces/${encodeURIComponent(workspaceId)}/members/${encodeURIComponent(userId)}`, {
      method: "DELETE",
    }),

  // ---------------------------------------------------------------------------
  // Agent session management
  // ---------------------------------------------------------------------------
  listAgentSessions: (statusFilter?: string, limit = 100, offset = 0) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (statusFilter) params.set("status", statusFilter);
    return request<AgentSessionListResponse>(`/agent-sessions?${params}`);
  },
  createAgentSession: (title: string, metadata?: Record<string, unknown>) =>
    request<AgentSession>("/agent-sessions", {
      method: "POST",
      body: JSON.stringify({ title, metadata: metadata ?? {} }),
    }),
  updateAgentSession: (sessionId: string, data: { title?: string; status?: string }) =>
    request<AgentSession>(`/agent-sessions/${encodeURIComponent(sessionId)}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteAgentSession: (sessionId: string) =>
    request<void>(`/agent-sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }),

  // ---------------------------------------------------------------------------
  // Auth session management (admin)
  // ---------------------------------------------------------------------------
  listActiveSessions: () => request<ActiveSessionListResponse>("/auth/active-sessions"),
  revokeUserSessions: (userId: string) =>
    request<{ revoked_count: number }>("/auth/revoke-sessions", {
      method: "POST",
      body: JSON.stringify({ user_id: userId }),
    }),
};
