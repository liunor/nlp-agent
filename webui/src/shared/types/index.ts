export type TurnStatus =
  | "accepted"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface AuthSession {
  user_id: string;
  workspace_ids: string[];
  roles: string[];
  csrf_token: string;
  expires_at: number;
}

export interface DeveloperSnapshot {
  runtime: Record<string, unknown>;
  features: Record<string, { available: boolean; reason: string }>;
  models: {
    defaults: Record<string, unknown>;
    routes: Record<string, unknown>;
    models: Record<string, Record<string, unknown>>;
    presets: Record<string, Record<string, unknown>>;
    providers: Record<string, Record<string, unknown>>;
  };
  tools: {
    catalog_revision: number;
    items: Array<Record<string, unknown>>;
    policies: Record<string, unknown>;
    mcp_servers: Record<string, Record<string, unknown>>;
    custom: Record<string, unknown>;
  };
  skills: Array<{ name: string; path: string; source: string; description: string; allowed_tools: string[]; capabilities: string[]; available: boolean; missing_requirements: string[]; bytes: number; modified_at: number }>;
  agents: Record<string, unknown>;
  workspace: { roots: Array<{ name: string; path: string; exists: boolean; writable: boolean }> };
  web: Record<string, unknown>;
}

export interface TeachingGoals {
  workspace_id: string;
  course_title: string;
  description: string;
  objectives: string[];
  focus_topics: string[];
  target_level: "beginner" | "intermediate" | "advanced";
}

export type AvailabilityStatus = "enabled" | "disabled";
export type BlueprintStatus = "draft" | AvailabilityStatus;
export interface KnowledgePoint { id: string; name: string; markdown: string; status: AvailabilityStatus; sort_order: number }
export interface CourseTopic { id: string; name: string; description: string; status: AvailabilityStatus; knowledge_points: KnowledgePoint[] }
export interface RubricPoint { id?: string; criterion: string; weight: number }
export interface ExerciseBlueprint { id: string; name: string; topic_id: string; knowledge_point_id: string; instructions: string; question_type: string; status: BlueprintStatus; rubric: RubricPoint[] }
export interface ReviewBlueprint { id: string; name: string; topic_id: string; knowledge_point_id: string; instructions: string; exercise_blueprint_id: string | null; status: BlueprintStatus; question_type: string; rubric: RubricPoint[] }
export interface GuidedBlueprint { id: string; name: string; topic_id: string; knowledge_point_id: string; guidance: string; status: BlueprintStatus }
export interface TeacherCatalog { workspace_id: string; topics: CourseTopic[]; exercise_blueprints: ExerciseBlueprint[]; review_blueprints: ReviewBlueprint[]; guided_blueprints: GuidedBlueprint[] }

export interface ClassifiedQuestion {
  turn_id: string;
  session_id: string;
  user_id: string;
  workspace_id: string;
  question: string;
  topic: string;
  question_type: string;
  difficulty: "beginner" | "intermediate" | "advanced";
  keywords: string[];
  status: string;
  created_at: string;
  has_error: boolean;
}

export interface TeacherDistribution { name: string; count: number; percentage: number }
export interface TeacherOverview {
  workspace_id: string;
  period_days: number;
  goals: TeachingGoals;
  revision: number;
  updated_at: string | null;
  summary: { questions: number; sessions: number; students: number; error_questions: number };
  questions: ClassifiedQuestion[];
  frequent_questions: Array<{ question: string; count: number; topic: string; question_type: string }>;
  weak_topics: Array<{ topic: string; score: number; questions: number; repeat_questions: number; errors: number; sessions: number; risk: "low" | "medium" | "high" }>;
  topic_distribution: TeacherDistribution[];
  difficulty_distribution: TeacherDistribution[];
  type_distribution: TeacherDistribution[];
}

export interface SessionSummary {
  session_id: string;
  user_id: string;
  workspace_id: string;
  channel: string;
  created_at?: number;
  last_active?: number;
}

export interface TurnRecord {
  turn_id: string;
  session_id: string;
  status: TurnStatus;
  input_text: string;
  final_text: string | null;
  error_kind: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  learning_context?: LearningContext | null;
  learning_progress?: Record<string, unknown> | null;
  exercise_state?: Record<string, unknown> | null;
}

export interface ServerEvent {
  v: "1";
  type: string;
  request_id?: string;
  event_id?: string;
  session_id?: string;
  turn_id?: string;
  sequence?: number;
  timestamp: string;
  payload: Record<string, unknown>;
}

export interface LearningContext {
  topic_id: string | null;
  topic_name: string;
  level: "beginner" | "intermediate" | "advanced";
  mode: "explain" | "socratic" | "practice" | "review";
}

export interface ActivityItem {
  id: string;
  kind: "thinking" | "tool" | "worker" | "recovery";
  label: string;
  status: "running" | "completed" | "error";
  detail?: string;
  startedAt?: string;
  completedAt?: string;
}

export interface ChatMessage {
  id: string;
  turnId: string;
  role: "user" | "assistant";
  content: string;
  reasoning?: string;
  status?: TurnStatus;
  activities?: ActivityItem[];
  createdAt: string;
  startedAt?: string;
  completedAt?: string;
}

export interface SessionLearningMeta {
  title?: string;
  topic?: string;
  categoryId?: string;
  favorite?: boolean;
  archived?: boolean;
  summary?: string;
  concepts?: string[];
  reviewConcepts?: string[];
  updatedAt?: number;
}

export interface LearningCategory {
  id: string;
  name: string;
  createdAt: number;
}

export interface LearningPreferences {
  version: 2;
  context: LearningContext;
  sessions: Record<string, SessionLearningMeta>;
  categories: LearningCategory[];
}

export interface UserSettings {
  locale: string;
  theme: "system" | "light" | "dark";
  show_reasoning: boolean;
  stream_render_interval_ms: number;
  model_profile: string;
  default_workspace_id?: string;
}

export interface RuntimeModelProfile {
  label: string;
  provider: string;
  available: boolean;
}

export interface SettingsRuntime {
  default_model_profile: string;
  model_profiles: Record<string, RuntimeModelProfile>;
}

// ---------------------------------------------------------------------------
// User management types
// ---------------------------------------------------------------------------

export interface UserProfile {
  id: string;
  username: string;
  display_name: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface UserListResponse {
  users: UserProfile[];
  total: number;
  offset: number;
  limit: number;
}

export interface MeResponse {
  user_id: string;
  username: string;
  display_name: string;
  status: string;
  roles: string[];
  workspace_ids: string[];
  permissions: string[];
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Workspace types
// ---------------------------------------------------------------------------

export interface Workspace {
  id: string;
  slug: string;
  name: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceListResponse {
  workspaces: Workspace[];
  total: number;
}

export interface WorkspaceMember {
  workspace_id: string;
  user_id: string;
  member_type: string;
  status: string;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Agent session types
// ---------------------------------------------------------------------------

export interface AgentSession {
  id: string;
  workspace_id: string;
  created_by_user_id: string;
  title: string;
  status: string;
  active_conversation_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentSessionListResponse {
  sessions: AgentSession[];
  total: number;
}

// ---------------------------------------------------------------------------
// Auth session management types
// ---------------------------------------------------------------------------

export interface ActiveSessionItem {
  id: string;
  user_id: string;
  workspace_id: string;
  expires_at: string;
  revoked_at: string | null;
  created_at: string;
}

export interface ActiveSessionListResponse {
  sessions: ActiveSessionItem[];
}

// ---------------------------------------------------------------------------
// RBAC / system administration
// ---------------------------------------------------------------------------

export interface RoleCatalogItem {
  code: string;
  name: string;
  description: string;
  status: string;
  is_builtin: boolean;
}

export interface PermissionCatalogItem {
  code: string;
  name: string;
  description: string;
  status: string;
}

export interface UserRolesResponse {
  user_id: string;
  role_codes: string[];
}

export interface RolePermissionsResponse {
  role_code: string;
  permissions: Record<string, string[]>;
}

export interface ClassroomItem {
  id: string;
  workspace_id: string;
  name: string;
  status: string;
}

export interface UpdateClassroomBody {
  name: string;
}

export interface MenuCatalogItem {
  id: string;
  parent_id: string | null;
  type: string;
  name: string;
  route_path: string | null;
  component_key: string | null;
  permission_id: string | null;
  client_scope: string | null;
  sort_order: number;
  visible: boolean;
  status: string;
}

export interface AuthorizationAuditItem {
  id: string;
  actor_user_id: string | null;
  target_user_id: string | null;
  decision: string;
  reason_code: string | null;
  permission_code: string | null;
  resource_type: string | null;
  resource_id: string | null;
  detail: Record<string, unknown> | null;
  created_at: string;
}

export interface RoleListResponse {
  items: RoleCatalogItem[];
}

export interface PermissionListResponse {
  items: PermissionCatalogItem[];
}

export interface ClassroomListResponse {
  items: ClassroomItem[];
}

export interface ClassroomMemberItem {
  classroom_id: string;
  user_id: string;
  username: string;
  display_name: string;
  member_role: "student" | "teacher";
  status: "active" | "disabled";
}

export interface ClassroomMemberListResponse {
  items: ClassroomMemberItem[];
}

export interface MenuListResponse {
  items: MenuCatalogItem[];
}

export interface AuthorizationAuditResponse {
  items: AuthorizationAuditItem[];
}
