import {
  Activity, AlertTriangle, CheckCircle2, CircleDot, Cpu, RefreshCw, ShieldAlert,
  TerminalSquare, TimerReset, Wifi,
} from "lucide-react";
import { useState } from "react";

export const MAX_SANDBOX_LOGS = 120;
export const SANDBOX_LOG_RETENTION_MS = 10 * 60 * 1000;
export const MAX_SANDBOX_CAPACITY_SAMPLES = 60;
export const SANDBOX_REFRESH_INTERVAL_MS = 5_000;
export const SANDBOX_HISTORY_WINDOWS = [
  { minutes: 10, label: "10 分钟" },
  { minutes: 30, label: "30 分钟" },
  { minutes: 120, label: "2 小时" },
] as const;

export interface SandboxCapacitySample {
  timestamp: number;
  ready: number;
  creating: number;
  target: number;
  deficit: number;
  assigned?: number;
  total?: number;
  total_max?: number;
  online_count?: number;
  unassigned_count?: number;
  active_session_count?: number;
  host_total?: number | null;
  host_total_max?: number | null;
  host_available?: number | null;
  host_budget_blocked_count?: number;
  role_demand?: Record<string, number>;
  adaptive_target?: number;
  target_source?: "adaptive" | "manual" | string;
  manual_target_expires_at?: number | null;
  arrival_rate_per_min?: number;
}

export interface SandboxOverview {
  runtime_states: Record<string, number>;
  capacity: {
    ready: number;
    creating: number;
    target: number;
    deficit: number;
    assigned?: number;
    total?: number;
    total_max?: number;
    execution_limit?: number;
    online_count?: number;
    active_session_count?: number;
    host_total?: number | null;
    host_total_max?: number | null;
    host_available?: number | null;
    host_budget_blocked_count?: number;
    unassigned_count?: number;
    role_demand?: Record<string, number>;
    adaptive_target?: number;
    target_source?: "adaptive" | "manual" | string;
    manual_target_expires_at?: number | null;
    arrival_rate_per_min?: number;
  };
  execution_latency: {
    sample_count: number;
    p50_ms: number | null;
    p95_ms: number | null;
    p99_ms: number | null;
  };
  active_executions: number;
  recent_failures: number;
  alerts: Array<{ code: string; severity: string; message: string }>;
  capacity_history: SandboxCapacitySample[];
  capacity_history_window_minutes?: number;
  sampled_at: string;
}

export interface SandboxRuntime {
  id: string;
  state: string;
  node_id: string | null;
  runtime_kind: string;
  resource_profile_id: string;
  external_runtime_id: string | null;
  failure_reason: string | null;
  updated_at: string | null;
  image_digest?: string | null;
  environment_id?: string | null;
  generation?: number;
  last_heartbeat_at?: string | null;
}

export interface SandboxExecution {
  id: string;
  owner_user_id: string;
  environment_id: string;
  runtime_instance_id: string | null;
  status: string;
  generation: number;
  started_at: string | null;
  completed_at: string | null;
  exit_reason: string | null;
  trace_id?: string | null;
  span_id?: string | null;
  parent_span_id?: string | null;
}

export interface SandboxExecutionEvent {
  event_id: string;
  seq: number | string;
  type: string;
  payload: Record<string, unknown>;
}

export interface SandboxPreloadCompatibility {
  available?: boolean;
  source?: string;
  profiles?: Record<string, {
    status?: string;
    image_digest?: string | null;
    checked_at?: string | null;
    reason?: string | null;
    [key: string]: unknown;
  }>;
  [key: string]: unknown;
}

export interface SandboxLogEntry {
  id: string;
  timestamp: string | null;
  level: string;
  event_type: string;
  execution_id?: string;
  runtime_id?: string;
  message: string;
}

const STATE_ORDER = ["ready_unbound", "assigned", "creating", "claiming", "draining", "failed"];
const STATE_LABELS: Record<string, string> = {
  ready_unbound: "待命",
  assigned: "已分配",
  creating: "创建中",
  claiming: "接管中",
  draining: "排空中",
  failed: "失败",
};
const ROLE_LABELS: Record<string, string> = {
  developer: "开发者",
  teacher: "教师",
  student: "学生",
  guest: "游客",
};

function timestamp(value: string | null | undefined): number {
  const parsed = value ? Date.parse(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : 0;
}

function finiteNumber(value: unknown, fallback = 0): number {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export interface SandboxPage<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
}

function capacityTimestamp(value: unknown): number {
  // The API stores Unix seconds; tolerate milliseconds so stale browser state
  // cannot move the chart back to 1970 after a hot reload.
  const parsed = finiteNumber(value);
  if (parsed <= 0) return 0;
  return parsed > 100_000_000_000 ? parsed / 1000 : parsed;
}

export function normalizeSandboxCapacitySamples(
  samples: ReadonlyArray<SandboxCapacitySample>,
): SandboxCapacitySample[] {
  const unique = new Map<number, SandboxCapacitySample>();
  for (const sample of samples) {
    const raw = sample as unknown as Record<string, unknown>;
    const normalizedTimestamp = capacityTimestamp(raw.timestamp);
    if (!normalizedTimestamp) continue;
    const adaptiveTarget = finiteNumber(raw.adaptive_target, Number.NaN);
    unique.set(normalizedTimestamp, {
      timestamp: normalizedTimestamp,
      ready: Math.max(0, finiteNumber(raw.ready)),
      creating: Math.max(0, finiteNumber(raw.creating)),
      target: Math.max(0, finiteNumber(raw.target)),
      deficit: Math.max(0, finiteNumber(raw.deficit)),
      ...(Number.isFinite(finiteNumber(raw.assigned, Number.NaN)) ? { assigned: Math.max(0, finiteNumber(raw.assigned)) } : {}),
      ...(Number.isFinite(finiteNumber(raw.total, Number.NaN)) ? { total: Math.max(0, finiteNumber(raw.total)) } : {}),
      ...(Number.isFinite(finiteNumber(raw.total_max, Number.NaN)) ? { total_max: Math.max(0, finiteNumber(raw.total_max)) } : {}),
      ...(Number.isFinite(finiteNumber(raw.online_count, Number.NaN)) ? { online_count: Math.max(0, finiteNumber(raw.online_count)) } : {}),
      ...(Number.isFinite(finiteNumber(raw.active_session_count, Number.NaN)) ? { active_session_count: Math.max(0, finiteNumber(raw.active_session_count)) } : {}),
      ...(Number.isFinite(finiteNumber(raw.host_total, Number.NaN)) ? { host_total: Math.max(0, finiteNumber(raw.host_total)) } : {}),
      ...(Number.isFinite(finiteNumber(raw.host_total_max, Number.NaN)) ? { host_total_max: Math.max(0, finiteNumber(raw.host_total_max)) } : {}),
      ...(Number.isFinite(finiteNumber(raw.host_available, Number.NaN)) ? { host_available: Math.max(0, finiteNumber(raw.host_available)) } : {}),
      ...(Number.isFinite(finiteNumber(raw.host_budget_blocked_count, Number.NaN)) ? { host_budget_blocked_count: Math.max(0, finiteNumber(raw.host_budget_blocked_count)) } : {}),
      ...(Number.isFinite(finiteNumber(raw.unassigned_count, Number.NaN)) ? { unassigned_count: Math.max(0, finiteNumber(raw.unassigned_count)) } : {}),
      ...(Number.isFinite(adaptiveTarget) ? { adaptive_target: Math.max(0, adaptiveTarget) } : {}),
      ...(typeof raw.target_source === "string" ? { target_source: raw.target_source } : {}),
      ...(Number.isFinite(finiteNumber(raw.manual_target_expires_at, Number.NaN)) ? { manual_target_expires_at: finiteNumber(raw.manual_target_expires_at) } : {}),
      ...(Number.isFinite(finiteNumber(raw.arrival_rate_per_min, Number.NaN))
        ? { arrival_rate_per_min: Math.max(0, finiteNumber(raw.arrival_rate_per_min)) }
        : {}),
    });
  }
  return [...unique.values()].sort((left, right) => left.timestamp - right.timestamp);
}

export function mergeSandboxCapacitySamples(
  current: SandboxCapacitySample[],
  incoming: SandboxCapacitySample[],
): SandboxCapacitySample[] {
  const normalizedCurrent = normalizeSandboxCapacitySamples(current);
  const normalizedIncoming = normalizeSandboxCapacitySamples(incoming);
  const unique = new Map<number, SandboxCapacitySample>();
  for (const sample of [...normalizedCurrent, ...normalizedIncoming]) {
    unique.set(sample.timestamp, sample);
  }

  return [...unique.values()]
    .sort((left, right) => left.timestamp - right.timestamp)
    .slice(-MAX_SANDBOX_CAPACITY_SAMPLES);
}

export function filterSandboxLogs(logs: SandboxLogEntry[]): SandboxLogEntry[] {
  return logs.filter((item) => {
    const eventType = item.event_type.toLowerCase();
    return item.level !== "debug"
      && !eventType.includes("heartbeat")
      && !eventType.includes("metrics")
      && item.message.trim().length > 0;
  });
}

export function mergeSandboxLogs(
  current: SandboxLogEntry[],
  incoming: SandboxLogEntry[],
  now = Date.now(),
): SandboxLogEntry[] {
  const cutoff = now - SANDBOX_LOG_RETENTION_MS;
  const unique = new Map<string, SandboxLogEntry>();
  for (const item of filterSandboxLogs([...current, ...incoming])) {
    const itemTime = timestamp(item.timestamp);
    if (itemTime <= 0 || itemTime < cutoff) continue;
    if (!unique.has(item.id)) unique.set(item.id, item);
  }
  return [...unique.values()]
    .sort((left, right) => timestamp(right.timestamp) - timestamp(left.timestamp))
    .slice(0, MAX_SANDBOX_LOGS);
}

function number(value: number | null | undefined, suffix = ""): string {
  return value == null ? "—" : `${value.toLocaleString()}${suffix}`;
}

function dateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).format(new Date(value));
}

function levelLabel(level: string): string {
  return { error: "异常", warning: "注意", info: "信息" }[level] ?? level;
}

function CapacityChart({
  samples,
  historyMinutes,
  onHistoryMinutesChange,
}: {
  samples: SandboxCapacitySample[];
  historyMinutes: number;
  onHistoryMinutesChange: (minutes: number) => void;
}) {
  const normalized = normalizeSandboxCapacitySamples(samples);
  const end = normalized.at(-1)?.timestamp ?? 0;
  const cutoff = end > 0 ? end - historyMinutes * 60 : 0;
  const visible = normalized.filter((sample) => sample.timestamp >= cutoff).slice(-MAX_SANDBOX_CAPACITY_SAMPLES);
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const width = 960;
  const height = 270;
  const padding = { top: 22, right: 28, bottom: 32, left: 42 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const max = Math.max(1, ...visible.map((sample) => Math.max(sample.target, sample.ready, sample.creating, sample.adaptive_target ?? 0)));
  const point = (value: number, index: number) => ({
    x: padding.left + (visible.length > 1 ? index / (visible.length - 1) : 0) * chartWidth,
    y: padding.top + chartHeight - (value / max) * chartHeight,
  });
  const path = (key: "ready" | "target" | "creating" | "adaptive_target") => visible.map((sample, index) => {
    const value = key === "adaptive_target" ? sample.adaptive_target ?? sample.target : sample[key];
    const item = point(value, index);
    return `${index === 0 ? "M" : "L"}${item.x.toFixed(1)},${item.y.toFixed(1)}`;
  }).join(" ");
  const latest = visible.at(-1);
  const latestPoint = latest ? point(latest.ready, visible.length - 1) : null;
  const activeSample = activeIndex == null ? undefined : visible[activeIndex];
  const activePoint = activeSample ? point(activeSample.ready, activeIndex!) : null;
  const tooltipPlacement = activePoint && activePoint.y < padding.top + 62 ? "below" : "above";
  const tooltipLeft = activePoint ? Math.min(88, Math.max(12, activePoint.x / width * 100)) : 50;
  const activeLabel = activeSample
    ? `${dateTime(new Date(activeSample.timestamp * 1000).toISOString())}：待命 ${number(activeSample.ready)}，目标 ${number(activeSample.target)}，自适应 ${number(activeSample.adaptive_target ?? activeSample.target)}，创建中 ${number(activeSample.creating)}`
    : "";

  return <div className="sandbox-capacity-chart">
    <div className="sandbox-chart-toolbar"><div className="sandbox-chart-legend"><span><i className="ready" />待命实例</span><span><i className="target" />目标容量</span><span><i className="adaptive" />自适应目标</span><span><i className="creating" />创建中</span><small>{visible.length ? `最近 ${visible.length} 个采样` : "等待采样"}</small></div><div className="sandbox-chart-range" role="group" aria-label="容量趋势时间范围">{SANDBOX_HISTORY_WINDOWS.map((window) => <button key={window.minutes} type="button" className={historyMinutes === window.minutes ? "active" : ""} aria-pressed={historyMinutes === window.minutes} onClick={() => onHistoryMinutesChange(window.minutes)}>{window.label}</button>)}</div></div>
    {!visible.length ? <div className="sandbox-chart-empty"><Activity size={18} />等待采样</div> : <div className="sandbox-chart-stage"><svg role="img" aria-label="Sandbox 容量实时趋势" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <title>Sandbox 容量实时趋势</title>
      <defs><linearGradient id="sandbox-ready-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#695bd7" stopOpacity=".22" /><stop offset="1" stopColor="#695bd7" stopOpacity="0" /></linearGradient></defs>
      {[0, 1, 2, 3, 4].map((line) => { const y = padding.top + chartHeight - line / 4 * chartHeight; return <line key={line} x1={padding.left} x2={width - padding.right} y1={y} y2={y} className="sandbox-chart-grid" />; })}
      <text x={padding.left - 8} y={padding.top + 4} textAnchor="end">{number(max)}</text>
      <text x={padding.left - 8} y={padding.top + chartHeight + 4} textAnchor="end">0</text>
      {visible.length > 1 && <path d={`${path("ready")} L${padding.left + chartWidth},${padding.top + chartHeight} L${padding.left},${padding.top + chartHeight} Z`} className="sandbox-chart-area" />}
      <path d={path("target")} className="sandbox-chart-line target" />
      <path d={path("adaptive_target")} className="sandbox-chart-line adaptive" />
      <path d={path("creating")} className="sandbox-chart-line creating" />
      <path d={path("ready")} className="sandbox-chart-line ready" />
      {visible.map((sample, index) => {
        const readyPoint = point(sample.ready, index);
        const label = `${dateTime(new Date(sample.timestamp * 1000).toISOString())}：待命 ${number(sample.ready)}，目标 ${number(sample.target)}，自适应 ${number(sample.adaptive_target ?? sample.target)}，创建中 ${number(sample.creating)}`;
        return <g key={`${sample.timestamp}-${index}`} className="sandbox-chart-point-hit" tabIndex={0} role="button" aria-label={label} onMouseEnter={() => setActiveIndex(index)} onMouseLeave={() => setActiveIndex((current) => current === index ? null : current)} onFocus={() => setActiveIndex(index)} onBlur={() => setActiveIndex((current) => current === index ? null : current)}><title>{label}</title><rect x={Math.max(padding.left, readyPoint.x - 24)} y={padding.top} width="48" height={chartHeight} className="sandbox-chart-hit" /><circle cx={readyPoint.x} cy={readyPoint.y} r={index === visible.length - 1 ? 4 : 3} className="sandbox-chart-dot" /></g>;
      })}
      {latestPoint && <circle cx={latestPoint.x} cy={latestPoint.y} r="8" className="sandbox-chart-pulse" />}
      <text x={padding.left} y={height - 8}>{dateTime(new Date(visible[0].timestamp * 1000).toISOString())}</text>
      <text x={width - padding.right} y={height - 8} textAnchor="end">{dateTime(new Date(visible.at(-1)!.timestamp * 1000).toISOString())}</text>
    </svg>{activeSample && activePoint ? <div className="sandbox-chart-tooltip" data-placement={tooltipPlacement} style={{ left: `${tooltipLeft}%`, top: `${activePoint.y / height * 100}%` }} role="status" aria-live="polite"><strong>{activeLabel}</strong><span>缺口 {number(activeSample.deficit)} · 到达率 {number(activeSample.arrival_rate_per_min, " /min")}</span></div> : null}{visible.length < 2 && <div className="sandbox-chart-hint"><Activity size={15} />正在积累趋势样本</div>}</div>}
  </div>;
}

function MetricCard({ icon: Icon, label, value, hint, tone = "default" }: { icon: typeof Activity; label: string; value: string; hint: string; tone?: string }) {
  return <article className={`sandbox-metric-card ${tone}`}><div className="sandbox-metric-icon"><Icon size={18} /></div><div><span>{label}</span><strong>{value}</strong><small>{hint}</small></div></article>;
}

function EmptyState({ text }: { text: string }) {
  return <div className="sandbox-empty-state"><CircleDot size={18} /><span>{text}</span></div>;
}

export function SandboxMonitorPage({
  overview,
  logs,
  runtimes,
  executions,
  runtimeTotal = runtimes.length,
  executionTotal = executions.length,
  runtimeHasMore = false,
  executionHasMore = false,
  historyMinutes = 30,
  onHistoryMinutesChange,
  listLoading = false,
  live,
  loading,
  logLoading,
  error = "",
  onRefresh,
  onLoadMoreRuntimes = () => undefined,
  onLoadMoreExecutions = () => undefined,
  onDrain,
  onOpenRuntime,
  runtimeDetail,
  onCloseRuntimeDetail,
  onOpenExecution,
  selectedExecution,
  executionEvents,
  onCloseExecutionEvents,
  preloadMatrix,
  onPrewarm,
  prewarmLoading = false,
  prewarmResult,
}: {
  overview: SandboxOverview | null;
  logs: SandboxLogEntry[];
  runtimes: SandboxRuntime[];
  executions: SandboxExecution[];
  runtimeTotal?: number;
  executionTotal?: number;
  runtimeHasMore?: boolean;
  executionHasMore?: boolean;
  historyMinutes?: number;
  listLoading?: boolean;
  live: boolean;
  loading: boolean;
  logLoading: boolean;
  error?: string;
  onRefresh: () => void;
  onLoadMoreRuntimes?: () => void;
  onLoadMoreExecutions?: () => void;
  onHistoryMinutesChange?: (minutes: number) => void;
  onDrain: (runtimeId: string) => void;
  onOpenRuntime?: (runtimeId: string) => void;
  runtimeDetail?: SandboxRuntime | null;
  onCloseRuntimeDetail?: () => void;
  onOpenExecution?: (executionId: string) => void;
  selectedExecution?: SandboxExecution | null;
  executionEvents?: SandboxExecutionEvent[];
  onCloseExecutionEvents?: () => void;
  preloadMatrix?: SandboxPreloadCompatibility | null;
  onPrewarm?: (body: { expected_sessions: number; sessions_per_runtime: number; ttl_seconds: number }) => void;
  prewarmLoading?: boolean;
  prewarmResult?: { target?: number; ttl_seconds?: number; command_id?: string } | null;
}) {
  const [prewarmOpen, setPrewarmOpen] = useState(false);
  const [expectedSessions, setExpectedSessions] = useState(4);
  const [sessionsPerRuntime, setSessionsPerRuntime] = useState(1);
  const [prewarmTtl, setPrewarmTtl] = useState(900);
  const visibleStates = STATE_ORDER.map((state) => [state, overview?.runtime_states[state] ?? 0] as const);
  const visibleLogs = filterSandboxLogs(logs);
  const roleDemand = Object.entries(overview?.capacity.role_demand ?? {})
    .filter(([, count]) => count > 0)
    .sort(([left], [right]) => (Object.keys(ROLE_LABELS).indexOf(left) - Object.keys(ROLE_LABELS).indexOf(right)));
  return <div className="sandbox-monitor-page">
    <section className="sandbox-monitor-hero">
      <div><span className="sandbox-eyebrow">SANDBOX OPERATIONS</span><h2>代码沙箱监控</h2><p>集中查看预热池、运行时和执行健康度。页面只保留可操作的摘要，原始代码与标准输出不会进入监控流。</p></div>
      <div className="sandbox-monitor-hero-actions"><span className={`sandbox-live-status ${live ? "online" : "offline"}`}><i />{live ? "实时同步" : "等待连接"}</span><button type="button" onClick={() => setPrewarmOpen((open) => !open)}>{prewarmOpen ? "收起预热" : "手动预热"}</button><button type="button" onClick={onRefresh} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新</button></div>
    </section>
    {prewarmOpen && <section className="sandbox-action-panel" aria-label="手动预热"><div><strong>一次性预热</strong><small>目标在 TTL 到期后自动恢复为自适应容量，不会永久覆盖动态策略。</small></div><label>预计会话数<input type="number" min="0" max="100000" value={expectedSessions} onChange={(event) => setExpectedSessions(Number(event.target.value))} /></label><label>每个 Runtime 会话数<input type="number" min="1" max="100" value={sessionsPerRuntime} onChange={(event) => setSessionsPerRuntime(Number(event.target.value))} /></label><label>目标 TTL（秒）<input type="number" min="60" max="86400" value={prewarmTtl} onChange={(event) => setPrewarmTtl(Number(event.target.value))} /></label><button type="button" disabled={prewarmLoading || !onPrewarm} onClick={() => onPrewarm?.({ expected_sessions: expectedSessions, sessions_per_runtime: sessionsPerRuntime, ttl_seconds: prewarmTtl })}>{prewarmLoading ? "提交中…" : "提交预热"}</button>{prewarmResult && <span className="sandbox-action-success">已排队：目标 {number(prewarmResult.target)}，TTL {number(prewarmResult.ttl_seconds, " 秒")}</span>}</section>}
    {error && <div className="sandbox-monitor-error"><AlertTriangle size={17} /><span>{error}</span></div>}
    {loading && !overview ? <div className="sandbox-monitor-loading"><RefreshCw className="spin" /><span>正在读取沙箱运行状态…</span></div> : overview && <>
      <div className="sandbox-metric-grid">
        <MetricCard icon={Cpu} label="预热池" value={`${number(overview.capacity.ready)} / ${number(overview.capacity.target)}`} hint={`${overview.capacity.target_source === "manual" ? "手动目标" : "自适应目标"} · ${overview.capacity.deficit ? `缺口 ${overview.capacity.deficit} 个` : `环境上限 ${number(overview.capacity.total_max)}`}`} tone={overview.capacity.deficit ? "warning" : "success"} />
        <MetricCard icon={Activity} label="运行中" value={number(overview.active_executions)} hint={`在线 ${number(overview.capacity.online_count)} · 会话 ${number(overview.capacity.active_session_count)} · Runtime ${number(overview.capacity.assigned)}`} tone="accent" />
        <MetricCard icon={TimerReset} label="P95 执行耗时" value={number(overview.execution_latency.p95_ms, " ms")} hint={`P50 ${number(overview.execution_latency.p50_ms, " ms")}`} />
        <MetricCard icon={ShieldAlert} label="近期故障" value={number(overview.recent_failures)} hint={`${overview.execution_latency.sample_count} 个完成样本`} tone={overview.recent_failures ? "danger" : "success"} />
      </div>
      <div className="sandbox-capacity-budget"><div><span>环境 Runtime</span><strong>{number(overview.capacity.total)} / {number(overview.capacity.total_max)}</strong></div><div><span>主机 Runtime</span><strong>{number(overview.capacity.host_total)} / {number(overview.capacity.host_total_max)}</strong></div><div><span>主机可用预算</span><strong>{number(overview.capacity.host_available)}</strong></div><div><span>预算阻塞</span><strong>{number(overview.capacity.host_budget_blocked_count)} 次</strong></div></div>
      <section className="sandbox-monitor-panel sandbox-capacity-panel"><header><div><span className="sandbox-section-kicker">CAPACITY SIGNAL</span><h3>容量实时趋势</h3><p>服务端按真实采样点更新，页面刷新不会制造伪造的时间点。</p></div><span className="sandbox-refresh-hint"><Wifi size={14} />每 5 秒同步 · {dateTime(overview.sampled_at)}</span></header><CapacityChart samples={overview.capacity_history} historyMinutes={historyMinutes} onHistoryMinutesChange={onHistoryMinutesChange ?? (() => undefined)} /></section>
      <div className="sandbox-monitor-columns">
        <section className="sandbox-monitor-panel sandbox-health-panel"><header><div><span className="sandbox-section-kicker">RUNTIME HEALTH</span><h3>运行时健康</h3><p>当前实例按生命周期状态聚合。</p></div><span className="sandbox-health-total"><TerminalSquare size={14} />{runtimes.length} 实例</span></header><div className="sandbox-state-grid">{visibleStates.map(([state, count]) => <div key={state}><span className={`sandbox-state-dot ${state}`} /><span>{STATE_LABELS[state] ?? state}</span><strong>{count}</strong></div>)}</div>{roleDemand.length > 0 && <div className="sandbox-demand-strip"><span>等待分配</span>{roleDemand.map(([role, count]) => <b key={role}>{ROLE_LABELS[role] ?? role} {count}</b>)}</div>}{overview.alerts.length > 0 && <div className="sandbox-alert-list">{overview.alerts.map((alert) => <div key={alert.code} className={alert.severity === "critical" ? "critical" : "warning"}><AlertTriangle size={15} /><span><strong>{alert.message}</strong><small>{alert.severity === "critical" ? "需要立即处理" : "需要关注"}</small></span></div>)}</div>}{!overview.alerts.length && <div className="sandbox-all-clear"><CheckCircle2 size={17} /><span>当前没有容量告警</span></div>}</section>
        <section className="sandbox-monitor-panel sandbox-log-panel"><header><div><span className="sandbox-section-kicker">SIGNAL STREAM</span><h3>运行日志</h3><p>只显示异常和状态变化，自动清理 10 分钟以前的记录。</p></div><span className="sandbox-log-count">{logLoading ? "同步中…" : `${visibleLogs.length} 条`}</span></header><div className="sandbox-log-list" aria-live="polite">{visibleLogs.map((item) => <article key={item.id}><span className={`sandbox-log-level ${item.level}`}>{levelLabel(item.level)}</span><div><strong>{item.message}</strong><small>{dateTime(item.timestamp)} · {item.event_type}{item.runtime_id ? ` · ${item.runtime_id.slice(0, 10)}` : ""}</small></div></article>)}{!visibleLogs.length && <EmptyState text="暂无需要关注的运行日志" />}</div></section>
      </div>
      <div className="sandbox-monitor-columns lower">
        <section className="sandbox-monitor-panel sandbox-inventory-panel"><header><div><span className="sandbox-section-kicker">RUNTIME INVENTORY</span><h3>运行时实例</h3></div><span className="sandbox-health-total">已加载 {runtimes.length} / {runtimeTotal}</span></header><div className="sandbox-runtime-list">{runtimes.map((runtime) => <article key={runtime.id}><span className={`sandbox-state-dot ${runtime.state}`} /><div><strong>{runtime.id.slice(0, 16)}</strong><small>{STATE_LABELS[runtime.state] ?? runtime.state} · {runtime.node_id ?? "未绑定节点"}</small></div><button type="button" onClick={() => onOpenRuntime?.(runtime.id)}>详情</button>{["assigned", "ready_unbound", "claiming"].includes(runtime.state) && <button type="button" onClick={() => onDrain(runtime.id)}>排空</button>}</article>)}{!runtimes.length && <EmptyState text="暂无运行时实例" />}</div>{runtimeHasMore && <button className="sandbox-load-more" type="button" onClick={onLoadMoreRuntimes} disabled={listLoading}>{listLoading ? "加载中…" : "加载更多运行时"}</button>}</section>
        <section className="sandbox-monitor-panel sandbox-execution-panel"><header><div><span className="sandbox-section-kicker">EXECUTION QUEUE</span><h3>最近执行</h3></div><span className="sandbox-health-total">已加载 {executions.length} / {executionTotal}</span></header><div className="sandbox-execution-list">{executions.map((execution) => <article key={execution.id}><span className={`sandbox-execution-status ${execution.status}`} /> <div><strong>{execution.id.slice(0, 16)}</strong><small>{execution.status} · {dateTime(execution.started_at)}</small></div><span className="sandbox-execution-runtime">{execution.runtime_instance_id?.slice(0, 10) ?? "—"}</span><button type="button" onClick={() => onOpenExecution?.(execution.id)}>事件</button></article>)}{!executions.length && <EmptyState text="暂无执行记录" />}</div>{executionHasMore && <button className="sandbox-load-more" type="button" onClick={onLoadMoreExecutions} disabled={listLoading}>{listLoading ? "加载中…" : "加载更多执行记录"}</button>}</section>
      </div>
      <section className="sandbox-monitor-columns lower sandbox-management-panels"><section className="sandbox-monitor-panel"><header><div><span className="sandbox-section-kicker">PRELOAD MATRIX</span><h3>预加载兼容矩阵</h3><p>{preloadMatrix?.source ?? "等待 Manager 返回矩阵"}</p></div><span className="sandbox-health-total">{preloadMatrix?.available === false ? "不可用" : "已加载"}</span></header><div className="sandbox-preload-list">{Object.entries(preloadMatrix?.profiles ?? {}).map(([profile, value]) => <article key={profile}><strong>{profile}</strong><span className={`sandbox-matrix-status ${value.status ?? "unknown"}`}>{value.status ?? "未知"}</span><small>{value.reason ?? value.image_digest ?? "未提供诊断"}</small></article>)}{!Object.keys(preloadMatrix?.profiles ?? {}).length && <EmptyState text="暂无兼容性记录" />}</div></section><section className="sandbox-monitor-panel sandbox-management-summary"><header><div><span className="sandbox-section-kicker">MANAGER DECISION</span><h3>当前容量决策</h3></div></header><dl><div><dt>决策来源</dt><dd>{overview.capacity.target_source === "manual" ? "手动预热（临时）" : "自适应策略"}</dd></div><div><dt>自适应目标</dt><dd>{number(overview.capacity.adaptive_target)}</dd></div><div><dt>到达率</dt><dd>{number(overview.capacity.arrival_rate_per_min, " /min")}</dd></div><div><dt>手动目标到期</dt><dd>{overview.capacity.manual_target_expires_at ? dateTime(new Date(overview.capacity.manual_target_expires_at * 1000).toISOString()) : "—"}</dd></div></dl></section></section>
      {runtimeDetail && <section className="sandbox-detail-panel"><header><div><span className="sandbox-section-kicker">RUNTIME DETAIL</span><h3>{runtimeDetail.id}</h3></div><button type="button" onClick={onCloseRuntimeDetail}>关闭</button></header><div className="sandbox-detail-grid"><span>状态<strong>{STATE_LABELS[runtimeDetail.state] ?? runtimeDetail.state}</strong></span><span>镜像<strong>{runtimeDetail.image_digest ?? "—"}</strong></span><span>环境<strong>{runtimeDetail.environment_id ?? "未绑定"}</strong></span><span>Generation<strong>{runtimeDetail.generation ?? "—"}</strong></span><span>最后心跳<strong>{dateTime(runtimeDetail.last_heartbeat_at)}</strong></span><span>失败原因<strong>{runtimeDetail.failure_reason ?? "—"}</strong></span></div></section>}
      {selectedExecution && <section className="sandbox-detail-panel"><header><div><span className="sandbox-section-kicker">EXECUTION EVENTS</span><h3>{selectedExecution.id}</h3></div><button type="button" onClick={onCloseExecutionEvents}>关闭</button></header><div className="sandbox-event-replay">{(executionEvents ?? []).map((event) => <article key={event.event_id}><div><strong>{event.type}</strong><small>seq {event.seq} · {event.event_id}</small></div><pre>{JSON.stringify(event.payload, null, 2)}</pre></article>)}{!(executionEvents ?? []).length && <EmptyState text="暂无事件回放" />}</div></section>}
    </>}
  </div>;
}
