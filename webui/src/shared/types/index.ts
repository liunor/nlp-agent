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
    profiles: Record<string, Record<string, unknown>>;
    default_model_profile: string | null;
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

export interface DeveloperRuntimeHealth {
  status: string;
  started: boolean;
  accepting_turns: boolean;
  active_turns: number;
  subscribers: number;
  database: string;
  durable_events: number;
}

export interface FeedbackMessage {
  id: string;
  sender_type: "student" | "developer";
  body: string;
  created_at: string;
}

export type FeedbackStatus = "open" | "under_review" | "planned" | "in_progress" | "complete" | "closed";
export type FeedbackCategory = "feature" | "ux" | "bug" | "other";
export type FeedbackPriority = "low" | "medium" | "high";

export interface FeedbackThreadSummary {
  thread_id: string;
  user_id: string;
  username: string;
  display_name: string;
  unread_count: number;
  updated_at: string;
  status: FeedbackStatus;
  category: FeedbackCategory;
  priority: FeedbackPriority;
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
  status: FeedbackStatus;
  category: FeedbackCategory;
  priority: FeedbackPriority;
  updated_at: string | null;
  student_unread_count?: number;
  messages: FeedbackMessage[];
  message_total?: number;
  message_offset?: number;
  message_limit?: number;
  message_has_more?: boolean;
}

export interface FeedbackDailyState {
  used: number;
  remaining: number;
  limit: number;
  today_start_utc: string;
}

export interface TeachingGoals {
  workspace_id: string;
  course_title: string;
  description: string;
  objectives: string[];
  focus_topics: string[];
  target_level: "beginner" | "intermediate" | "advanced";
}

export interface TeacherAnalysisAnnotations {
  workspace_id: string;
  focused: string[];
  ignored: string[];
  notes: Record<string, string>;
}

export interface TeacherDataCompleteness {
  complete: boolean;
  evidence_truncated: boolean;
  criterion_truncated: boolean;
  message: string | null;
}

export type AvailabilityStatus = "enabled" | "disabled";
export const DEFAULT_QUESTION_TYPES = ["简答", "选择题", "判断题", "填空题", "编程题", "代码阅读题", "计算题", "论述题"] as const;
export type BlueprintStatus = "draft" | AvailabilityStatus;
export interface KnowledgePoint { id: string; name: string; markdown: string; status: AvailabilityStatus; sort_order: number; question_types?: string[] }
export interface CourseTopic { id: string; name: string; description: string; status: AvailabilityStatus; knowledge_points: KnowledgePoint[] }
export interface RubricPoint { id?: string; criterion: string; weight: number }
export interface ExerciseBlueprint { id: string; name: string; topic_id: string; knowledge_point_id: string; instructions: string; question_type: string; status: BlueprintStatus; rubric: RubricPoint[] }
export interface ReviewBlueprint { id: string; name: string; topic_id: string; knowledge_point_id: string; instructions: string; exercise_blueprint_id: string | null; status: BlueprintStatus; question_type: string; rubric: RubricPoint[] }
export interface GuidedBlueprint { id: string; name: string; topic_id: string; knowledge_point_id: string; guidance: string; status: BlueprintStatus }
export interface TeacherCatalog { workspace_id: string; topics: CourseTopic[]; exercise_blueprints: ExerciseBlueprint[]; review_blueprints: ReviewBlueprint[]; guided_blueprints: GuidedBlueprint[] }
export interface TeacherBookNavigationItem {
  topic_id: string;
  topic_name: string;
  knowledge_point_id: string;
  title: string;
  sort_order: number;
  topic_status: AvailabilityStatus;
  knowledge_point_status: AvailabilityStatus;
  has_draft: boolean;
  has_published: boolean;
  revision: number;
  published_revision: number | null;
}
export interface LearningBookNavigationItem {
  topic_id: string;
  topic_name: string;
  knowledge_point_id: string;
  title: string;
  sort_order: number;
  revision: number;
}
export interface TeacherBookPage {
  workspace_id: string;
  topic_id: string;
  topic_name: string;
  knowledge_point_id: string;
  title: string;
  draft_markdown: string;
  published_markdown: string | null;
  revision: number;
  published_revision: number | null;
  updated_at: string | null;
}
export interface LearningBookPage {
  workspace_id: string;
  topic_id: string;
  topic_name: string;
  knowledge_point_id: string;
  title: string;
  content_markdown: string;
  revision: number;
}
export interface TeacherBookImportPreview {
  file_name: string;
  content_markdown: string;
  removed_frameworks: string[];
  warnings: string[];
}
export interface TeacherBookAssetInput {
  asset_path: string;
  media_type: string;
  content_base64: string;
}

export interface TeacherBookArchiveItemPreview {
  topic_id: string;
  knowledge_point_id: string;
  title: string;
  file_name: string;
  action: "create" | "update" | "unchanged";
  expected_revision: number;
  current_markdown: string;
  content_markdown: string;
  removed_frameworks: string[];
  warnings: string[];
}

export interface TeacherBookArchiveImportPreview {
  file_name: string;
  format_version: number;
  title: string;
  items: TeacherBookArchiveItemPreview[];
  asset_paths: string[];
  omitted_knowledge_points: string[];
  warnings: string[];
}

export interface TeacherDistribution { name: string; count: number; percentage: number }

export interface TeacherMonthlyQuestionStatistics {
  month: string;
  label: string;
  question_count: number;
  topic_distribution: TeacherDistribution[];
  difficulty_distribution: TeacherDistribution[];
  mode_distribution: TeacherDistribution[];
  daily_questions: Array<{ day: number; date: string; count: number }>;
  hourly_questions: Array<{ hour: number; label: string; count: number; percentage: number }>;
}

export interface TeacherStudentActivity {
  user_id: string;
  display_name: string;
  username: string | null;
  questions: number;
  sessions: number;
  active_days: number;
  error_questions: number;
  error_rate: number;
  questions_per_session: number;
  last_active: string | null;
  top_topic: string;
}

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

export type LearningAnalysisTrend = "up" | "down" | "stable";
export type LearningAnalysisProblemType = "概念掌握不足" | "解题方法不熟" | "易错点集中" | "练习覆盖不足" | "学习参与不足" | "数据不足，暂不判断" | "—";

export interface LearningAnalysisRecommendation {
  conclusion: string;
  action: string;
}

export interface LearningAnalysisDiagnosis {
  content_id: string;
  content_name: string;
  knowledge_point_id: string;
  knowledge_point_name: string;
  question_count: number;
  student_count: number;
  attempt_count: number;
  correct_count: number;
  mastery_rate: number | null;
  /** Granularity of mastery_rate/average_score: currently exercise-level (整题级). */
  mastery_basis?: "exercise";
  previous_mastery_rate: number | null;
  trend: LearningAnalysisTrend;
  problem_type: LearningAnalysisProblemType;
  data_sufficiency: "sufficient" | "insufficient";
  average_score: number | null;
  weak_criteria: Array<{ criterion: string; error_rate: number }>;
  concern_score: number;
  error_count: number;
  repeated_error_student_count: number;
  question_examples: Array<{ question_id: string; question: string; score: number | null; passed: boolean }>;
  recommendation: LearningAnalysisRecommendation;
}

export interface TeacherLearningAnalysis {
  scope: {
    period_days: number;
    period_label: string;
    role_label: "学生";
    student_count: number;
    attempt_count: number;
  };
  conclusions: {
    weak: LearningAnalysisDiagnosis | null;
    declining: LearningAnalysisDiagnosis | null;
    good: LearningAnalysisDiagnosis | null;
  };
  diagnoses: LearningAnalysisDiagnosis[];
  problem_distribution: TeacherDistribution[];
  mastery_trend: {
    months: Array<{ month: string; label: string }>;
    series: Array<{ knowledge_point_id: string; name: string; values: Array<number | null> }>;
  };
}

export interface TeacherAIDiagnosis {
  knowledge_point_id: string;
  knowledge_point_name: string;
  level: "high" | "medium" | "low";
  problem: string;
  cause: string;
  evidence: string[];
  suggestions: string[];
  confidence: "high" | "medium" | "low";
  data_gaps: string[];
  error_type: string;
  question_examples: Array<{ question_id: string; question: string; score: number | null; passed: boolean }>;
}

export interface TeacherAIAnalysisResult {
  status: "completed" | "failed";
  source: "deepseek" | "rules";
  message: string;
  summary: string;
  diagnoses: TeacherAIDiagnosis[];
  generated_at: string;
  model: string;
  model_id: string | null;
  cache_hit: boolean;
  scope?: TeacherLearningAnalysis["scope"];
  course_id?: string;
  content_scope?: string;
  start_date?: string;
  end_date?: string;
  data_version?: string;
}

export interface TeacherOverview {
  workspace_id: string;
  period_days: number;
  goals: TeachingGoals;
  annotations: TeacherAnalysisAnnotations;
  revision: number;
  updated_at: string | null;
  summary: {
    questions: number;
    sessions: number;
    students: number;
    active_days: number;
    error_questions: number;
    error_rate: number;
    questions_per_student: number;
    questions_per_session: number;
    contextualized_questions: number;
    context_coverage_rate: number;
    exercises: number;
    exercise_pass_rate: number;
    guided_sessions: number;
  };
  topic_distribution: TeacherDistribution[];
  difficulty_distribution: TeacherDistribution[];
  mode_distribution: TeacherDistribution[];
  daily_questions: Array<{ date: string; count: number }>;
  hourly_questions: Array<{ hour: number; label: string; count: number; percentage: number }>;
  weekday_questions: Array<{ weekday: number; label: string; count: number; percentage: number }>;
  peak_day: { date: string; count: number } | null;
  peak_hour: { hour: number; label: string; count: number } | null;
  monthly_statistics?: TeacherMonthlyQuestionStatistics[];
  student_activity: TeacherStudentActivity[];
  weak_topics: WeakTopic[];
  knowledge_point_stats: KnowledgePointStat[];
  learning_analysis?: TeacherLearningAnalysis;
  truncated?: boolean;
  data_completeness?: TeacherDataCompleteness;
}

export interface SessionSummary {
  session_id: string;
  user_id: string;
  workspace_id: string;
  channel: string;
  created_at?: string | number;
  last_active?: string | number;
  title?: string;
  title_is_manual?: boolean;
}

export interface SessionListResponse {
  items: SessionSummary[];
  total?: number;
  offset?: number;
  limit?: number;
  has_more?: boolean;
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
  roles?: string[];
}

export interface RbacRole { code: string; name: string; description: string; status: string; is_builtin: boolean }
export interface RbacPermission { code: string; name: string; description: string; status: string }
export interface SystemMenu { id: string; parent_id: string | null; type: string; name: string; route_path: string | null; component_key: string | null; permission_id: string | null; client_scope: string | null; sort_order: number; visible: boolean; status: string }
export interface AuthorizationAuditRecord { id: string; actor_user_id: string | null; target_user_id: string | null; decision: string; reason_code: string; permission_code: string | null; resource_type: string | null; resource_id: string | null; detail: Record<string, unknown>; created_at: string }
export interface AuthorizationAuditListResponse { items: AuthorizationAuditRecord[]; total: number; offset: number; limit: number; has_more: boolean }
export interface AuthorizationAuditSummary { period_days: number; since: string; total: number; by_decision: Record<string, number>; top_reasons: Array<{ reason_code: string; count: number }> }

export interface QuotaBucketSnapshot {
  owner_type: "user" | "workspace" | "classroom";
  owner_id: string;
  bucket_type: "daily" | "weekly";
  limit_micro: number | null;
  grant_micro: number;
  adjustment_micro: number;
  consumed_micro: number;
  reserved_micro: number;
  remaining_micro: number;
  reset_at: string;
  over_limit: boolean;
}
export interface QuotaSnapshot {
  user_id: string;
  workspace_id: string | null;
  buckets: QuotaBucketSnapshot[];
}
export interface QuotaUsageBreakdown {
  day: string;
  period_start?: string;
  period_end?: string;
  granularity?: "day" | "week";
  purpose: string;
  provider: string;
  provider_model: string;
  events: number;
  priced_events: number;
  unpriced_events: number;
  priced_credits_micro: number;
  total_tokens: number;
  input_tokens?: number;
  output_tokens?: number;
  reasoning_output_tokens?: number;
}
export interface QuotaUsageSnapshot {
  user_id: string;
  workspace_id: string | null;
  period_days: number;
  from: string;
  to: string;
  granularity: "day" | "week";
  events: number;
  priced_events: number;
  unpriced_events: number;
  credits_complete: boolean;
  credit_status: string;
  credits_micro: number | null;
  priced_credits_micro: number;
  tokens: Record<string, number>;
  breakdown: QuotaUsageBreakdown[];
}
export interface QuotaPolicy {
  policy_id: string;
  code: string;
  version: string;
  name: string;
  status: string;
  request_limit_micro: number | null;
  daily_limit_micro: number | null;
  weekly_limit_micro: number | null;
  concurrency_limit: number | null;
  max_overdraft_micro: number;
  allowed_model_profiles: string[];
  unlimited: boolean;
  effective_from: string;
  effective_until: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}
export interface QuotaPolicyUpdateInput {
  code?: string;
  version?: string;
  name?: string;
  request_limit_micro?: number | null;
  daily_limit_micro?: number | null;
  weekly_limit_micro?: number | null;
  concurrency_limit?: number | null;
  max_overdraft_micro?: number;
  allowed_model_profiles?: string[];
  unlimited?: boolean;
  effective_from?: string;
  effective_until?: string | null;
}
export interface QuotaPricingRule {
  pricing_rule_id: string;
  pricing_key: string;
  version: string;
  effective_from: string;
  effective_until: string | null;
  ordinary_input_credits_micro_per_million_tokens: number;
  cached_input_credits_micro_per_million_tokens: number;
  cache_write_credits_micro_per_million_tokens: number;
  output_credits_micro_per_million_tokens: number;
  reasoning_output_credits_micro_per_million_tokens: number | null;
  status: string;
  created_by: string;
  created_at: string;
}
export interface QuotaBinding {
  binding_id: string;
  subject_type: string;
  subject_id: string;
  policy_id: string;
  policy_code: string;
  policy_version: string;
  priority: number;
  status: string;
  effective_from: string;
  effective_until: string | null;
}
export interface QuotaGrant {
  grant_id: string;
  owner_type: string;
  owner_id: string;
  bucket_type: string;
  period_start: string;
  period_end: string;
  source_type: string;
  source_id: string | null;
  allocated_micro: number;
  effective_from: string;
  expires_at: string | null;
  status: string;
  reason: string;
  created_by: string;
  idempotency_key: string;
  revoked_at: string | null;
  revoked_by: string | null;
  revocation_idempotency_key: string | null;
  created_at: string;
}
export interface QuotaAdjustment {
  adjustment_id: string;
  owner_type: string;
  owner_id: string;
  bucket_type: string;
  period_start: string;
  period_end: string;
  amount_micro: number;
  actor_user_id: string;
  reason: string;
  idempotency_key: string;
  created_at: string;
}
export interface QuotaDailyRollup {
  rollup_date: string;
  user_id: string;
  workspace_id: string | null;
  provider: string;
  provider_model: string;
  purpose: string;
  events: number;
  exact_events: number;
  estimated_events: number;
  pending_events: number;
  unavailable_events: number;
  priced_credits_micro: number;
  tokens: Record<string, number>;
}
export interface QuotaBillingRecord {
  billing_id: string;
  provider: string;
  statement_id: string;
  operation_id: string;
  billed_at: string;
  billed_credits_micro: number | null;
  billed_tokens: Record<string, number>;
  matched_usage_event_id: string | null;
  local_credits_micro: number | null;
  difference_micro: number | null;
  status: string;
  idempotency_key: string;
  reconciled_at: string | null;
}
export interface QuotaBillingStatementInput {
  provider: string;
  statement_id: string;
  operation_id: string;
  billed_at: string;
  billed_credits_micro: number | null;
  billed_tokens: Record<string, number>;
  idempotency_key: string;
}
export interface QuotaCreditOperation {
  operation_id: string;
  operation_type: "gift" | "reset";
  owner_type: string;
  owner_id: string;
  bucket_type: string;
  period_start: string;
  period_end: string;
  amount_micro: number;
  grant_id?: string | null;
  effective_from: string;
  expires_at: string | null;
  reason: string;
  idempotency_key: string;
  status: string;
  recipient_count?: number;
  created_at?: string;
}
export interface QuotaCreditOperationInput {
  owner_type: "user" | "workspace" | "classroom";
  owner_id: string;
  bucket_type: "daily" | "weekly";
  period_start: string;
  period_end: string;
  amount_micro: number;
  reason: string;
  idempotency_key: string;
  effective_from: string;
  expires_at: string | null;
}
export interface QuotaRoleCreditOperationInput {
  role_code: string;
  bucket_type: "daily" | "weekly";
  period_start: string;
  period_end: string;
  amount_micro: number;
  reason: string;
  idempotency_key: string;
  effective_from: string;
  expires_at: string | null;
}
export interface QuotaRoleCreditOperationResult {
  operation_type: "gift";
  target_type: "role";
  target_id: string;
  recipient_count: number;
  items: QuotaCreditOperation[];
  idempotency_key: string;
}
export interface QuotaAlert {
  alert_id: string;
  alert_type: string;
  severity: string;
  owner_type: string;
  owner_id: string;
  window_start: string;
  window_end: string;
  baseline_micro: number;
  actual_micro: number;
  threshold_multiplier: number;
  status: string;
  metadata: Record<string, unknown>;
  resolved_at: string | null;
}
export interface QuotaBucketReplay {
  bucket_id: string;
  stored_consumed_micro: number;
  stored_reserved_micro: number;
  expected_consumed_micro: number;
  expected_reserved_micro: number;
  expected_over_limit: boolean;
  ledger_entries: number;
  needs_repair: boolean;
}
export interface QuotaBucketCandidate {
  bucket_id: string;
  owner_type: "user" | "workspace" | "classroom";
  owner_id: string;
  bucket_type: "daily" | "weekly";
  period_start: string;
  period_end: string;
  limit_micro: number | null;
  consumed_micro: number;
  reserved_micro: number;
  over_limit: boolean;
  updated_at: string;
}
export interface QuotaArchiveBatch {
  batch_id: string;
  cutoff_at: string;
  event_count: number;
  status: string;
  actor_user_id: string | null;
  created_at: string;
  completed_at: string | null;
}
export interface QuotaPolicyExplanation {
  user_id: string;
  workspace_id: string | null;
  evaluated_at: string;
  base: { policy_id: string; code: string; version: string; reason: { subject_type: string; subject_id: string; priority: number }; limits: Record<string, number | null> };
  workspace: { policy_id: string; code: string; version: string; reason: { subject_type: string; subject_id: string; priority: number }; limits: Record<string, number | null> } | null;
  candidates: Record<string, number>;
}

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
