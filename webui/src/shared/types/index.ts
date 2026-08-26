export type TurnStatus =
  | "accepted"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface AuthSession {
  user_id: string;
  username?: string;
  display_name?: string;
  workspace_ids: string[];
  roles: string[];
  csrf_token: string;
  expires_at: number;
  permissions?: string[];
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

export interface FeedbackMessage {
  id: string;
  sender_type: "student" | "developer";
  body: string;
  created_at: string;
}

export interface FeedbackThreadSummary {
  thread_id: string;
  user_id: string;
  username: string;
  display_name: string;
  unread_count: number;
  updated_at: string;
  latest: FeedbackMessage | null;
}

export interface FeedbackThreadList {
  items: FeedbackThreadSummary[];
  total: number;
}

export interface FeedbackThread {
  thread_id: string;
  user_id: string;
  username: string;
  display_name: string;
  messages: FeedbackMessage[];
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

export interface TeacherDistribution { name: string; count: number; percentage: number }

export interface WeakTopic {
  topic_id: string;
  topic: string;
  questions: number;
  errors: number;
  exercises: number;
  average_score: number | null;
  pass_rate: number | null;
  misconceptions: number;
  risk: "low" | "medium" | "high";
}

export interface KnowledgePointStat {
  knowledge_point_id: string;
  name: string;
  topic: string;
  exercises: number;
  average_score: number | null;
  pass_rate: number | null;
  weak_criteria: Array<{ criterion: string; hit_rate: number }>;
}

export interface TeacherOverview {
  workspace_id: string;
  period_days: number;
  goals: TeachingGoals;
  revision: number;
  updated_at: string | null;
  summary: {
    questions: number;
    sessions: number;
    students: number;
    error_questions: number;
    exercises: number;
    exercise_pass_rate: number;
    guided_sessions: number;
  };
  topic_distribution: TeacherDistribution[];
  difficulty_distribution: TeacherDistribution[];
  mode_distribution: TeacherDistribution[];
  daily_questions: Array<{ date: string; count: number }>;
  weak_topics: WeakTopic[];
  knowledge_point_stats: KnowledgePointStat[];
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

export interface ChatAttachment {
  fileName: string;
  displayName?: string;
  url: string;
  mediaType: string;
  width: number;
  height: number;
  status: "uploading" | "ready" | "error";
  progress?: number;
  errorMessage?: string;
}

export interface ChatMessage {
  id: string;
  turnId: string;
  role: "user" | "assistant";
  content: string;
  reasoning?: string;
  status?: TurnStatus;
  activities?: ActivityItem[];
  attachments?: ChatAttachment[];
  createdAt: string;
  startedAt?: string;
  completedAt?: string;
}

export interface SessionLearningMeta {
  title?: string;
  topic?: string;
  categoryId?: string;
  archived?: boolean;
  pinnedAt?: number;
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
    content_font_size: "small" | "medium" | "large";
  reduce_motion: boolean;
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

export interface ReleaseNoteEntry {
  id: string;
  version: string;
  released_at: string;
  notes: string[];
  status: "draft" | "published";
}

// ---- Admin module (用户 / 工作区 / 班级管理) ----
// 注意：UserProfile 不暴露任何密码字段（满足 review 7.2）。
export type UserStatus = "active" | "disabled" | "locked";

export interface UserProfile {
  id: string;
  username: string;
  display_name: string;
  status: UserStatus;
  created_at: string;
  updated_at: string;
  deleted_at?: string | null;
  last_login_at?: string | null;
}

export interface RbacRole { code: string; name: string; description: string; status: string; is_builtin: boolean }
export interface RbacPermission { code: string; name: string; description: string; status: string }
export interface SystemMenu { id: string; parent_id: string | null; type: string; name: string; route_path: string | null; component_key: string | null; permission_id: string | null; client_scope: string | null; sort_order: number; visible: boolean; status: string }
export interface AuthorizationAuditRecord { id: string; actor_user_id: string | null; target_user_id: string | null; decision: string; reason_code: string; permission_code: string | null; resource_type: string | null; resource_id: string | null; detail: Record<string, unknown>; created_at: string }

export interface UserListResponse {
  users: UserProfile[];
  total: number;
  offset: number;
  limit: number;
}

export interface Workspace {
  id: string;
  slug: string;
  name: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceMember {
  workspace_id: string;
  user_id: string;
  member_type: string;
  status: string;
  created_at: string;
}

export interface ClassroomSummary {
  id: string;
  workspace_id: string;
  name: string;
  status: string;
}

export interface JoinRequest {
  id: string;
  class_id: string;
  class_name: string;
  user_id: string;
  user_name: string;
  display_name: string;
  student_number: string | null;
  status: string;
  requested_at: string;
  reviewed_at: string | null;
  reviewed_by: string | null;
}

export interface JoinRequestListResponse {
  items: JoinRequest[];
  total: number;
}
