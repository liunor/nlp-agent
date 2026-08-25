import type { AuthSession, AuthorizationAuditRecord, DeveloperSnapshot, RbacPermission, RbacRole, ReleaseNoteEntry, SettingsRuntime, SystemMenu, TeacherCatalog, TeacherOverview, TeachingGoals, SessionSummary, TurnRecord, UserSettings, UserListResponse, Workspace, WorkspaceMember, ClassroomSummary, JoinRequest, JoinRequestListResponse } from "@/shared/types";

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
  }>("/sandbox/lease", { method: "POST" }),
  executeSandbox: (source: string) => request<{ status: string; stdout: string; stderr: string }>("/sandbox/execute", { method: "POST", body: JSON.stringify({ source }) }),
  restartSandbox: () => request<{ status: string }>("/sandbox/restart", { method: "POST" }),
  createWsTicket: () => request<{ ticket: string; expires_in: number }>("/auth/ws-ticket", { method: "POST", body: "{}" }),
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
    request<{ settings: Partial<UserSettings> }>("/settings", {
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
  listReleaseNotes: () => request<{ items: ReleaseNoteEntry[] }>("/developer/release-notes"),
  createReleaseNote: (note: Omit<ReleaseNoteEntry, "id">) =>
    request<ReleaseNoteEntry>("/developer/release-notes", { method: "POST", body: JSON.stringify(note) }),
  updateReleaseNote: (noteId: string, note: Omit<ReleaseNoteEntry, "id">) =>
    request<ReleaseNoteEntry>(`/developer/release-notes/${encodeURIComponent(noteId)}`, { method: "PUT", body: JSON.stringify(note) }),
  deleteReleaseNote: (noteId: string) => request<void>(`/developer/release-notes/${encodeURIComponent(noteId)}`, { method: "DELETE" }),
  listPublishedReleaseNotes: () => request<{ items: ReleaseNoteEntry[] }>("/learning/release-notes"),
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

  // ---- Admin module (用户 / 工作区 / 班级加入申请) ----
  listUsers: (offset = 0, limit = 20, status?: string, keyword?: string, includeDeleted = false) =>
    request<UserListResponse>(
      `/users?offset=${offset}&limit=${limit}${status ? `&status=${encodeURIComponent(status)}` : ""}${keyword ? `&keyword=${encodeURIComponent(keyword)}` : ""}${includeDeleted ? "&include_deleted=true" : ""}`,
    ),
  createUser: (input: { username: string; display_name: string; password: string }) =>
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
  createRole: (input: { code: string; name: string; description?: string }) =>
    request<RbacRole>("/system/roles", { method: "POST", body: JSON.stringify(input) }),
  updateRoleStatus: (roleCode: string, status: "active" | "disabled") =>
    request<void>(`/system/roles/${encodeURIComponent(roleCode)}/status`, { method: "PATCH", body: JSON.stringify({ status }) }),
  listPermissions: () => request<{ items: RbacPermission[] }>("/permissions"),
  listRolePermissions: (roleCode: string) => request<{ role_code: string; permissions: Record<string, string[]> }>(`/system/roles/${encodeURIComponent(roleCode)}/permissions`),
  replaceRolePermissions: (roleCode: string, permission_codes: string[], scopes: Record<string, string[]>) =>
    request<void>(`/system/roles/${encodeURIComponent(roleCode)}/permissions`, { method: "PUT", body: JSON.stringify({ permission_codes, scopes }) }),
  listMenus: () => request<{ items: SystemMenu[] }>("/system/menus"),
  listVisibleMenus: () => request<{ items: SystemMenu[] }>("/system/menus/visible"),
  replaceRoleMenus: (roleCode: string, menu_ids: string[]) =>
    request<void>(`/system/roles/${encodeURIComponent(roleCode)}/menus`, { method: "PUT", body: JSON.stringify({ menu_ids }) }),
  listRoleMenus: (roleCode: string) => request<{ role_code: string; menu_ids: string[] }>(`/system/roles/${encodeURIComponent(roleCode)}/menus`),
  listAuthorizationAudit: (limit = 100, actorUserId?: string) => request<{ items: AuthorizationAuditRecord[] }>(`/audit/authorization?limit=${limit}${actorUserId ? `&actor_user_id=${encodeURIComponent(actorUserId)}` : ""}`),
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
};
