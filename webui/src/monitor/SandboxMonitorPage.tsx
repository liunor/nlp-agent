import {
  Activity, AlertTriangle, CheckCircle2, CircleDot, Cpu, RefreshCw, ShieldAlert,
  TerminalSquare, TimerReset, Wifi,
} from "lucide-react";

export const MAX_SANDBOX_LOGS = 120;
export const SANDBOX_LOG_RETENTION_MS = 10 * 60 * 1000;
export const MAX_SANDBOX_CAPACITY_SAMPLES = 60;
export const SANDBOX_REFRESH_INTERVAL_MS = 2_000;

export interface SandboxCapacitySample {
  timestamp: number;
  ready: number;
  creating: number;
  target: number;
  deficit: number;
  adaptive_target?: number;
  arrival_rate_per_min?: number;
}

export interface SandboxOverview {
  runtime_states: Record<string, number>;
  capacity: {
    ready: number;
    creating: number;
    target: number;
    deficit: number;
    adaptive_target?: number;
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

function timestamp(value: string | null | undefined): number {
  const parsed = value ? Date.parse(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : 0;
}

function capacityTimestamp(value: number): number {
  // The API stores Unix seconds; tolerate milliseconds so stale browser state
  // cannot move the chart back to 1970 after a hot reload.
  return value > 100_000_000_000 ? value / 1000 : value;
}

export function mergeSandboxCapacitySamples(
  current: SandboxCapacitySample[],
  incoming: SandboxCapacitySample[],
  now = Date.now(),
): SandboxCapacitySample[] {
  const normalizedCurrent = current
    .filter((sample) => Number.isFinite(sample.timestamp))
    .map((sample) => ({ ...sample, timestamp: capacityTimestamp(sample.timestamp) }))
    .sort((left, right) => left.timestamp - right.timestamp);
  const normalizedIncoming = incoming
    .filter((sample) => Number.isFinite(sample.timestamp))
    .map((sample) => ({ ...sample, timestamp: capacityTimestamp(sample.timestamp) }))
    .sort((left, right) => left.timestamp - right.timestamp);
  const unique = new Map<number, SandboxCapacitySample>();
  for (const sample of [...normalizedCurrent, ...normalizedIncoming]) {
    unique.set(sample.timestamp, sample);
  }

  const latestCurrent = normalizedCurrent.at(-1);
  const latestIncoming = normalizedIncoming.at(-1);
  if (latestIncoming && (!latestCurrent || latestIncoming.timestamp <= latestCurrent.timestamp)) {
    const syntheticTimestamp = Math.max(now / 1000, (latestCurrent?.timestamp ?? 0) + 0.001);
    unique.set(syntheticTimestamp, { ...latestIncoming, timestamp: syntheticTimestamp });
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

function CapacityChart({ samples }: { samples: SandboxCapacitySample[] }) {
  const visible = samples
    .filter((sample) => Number.isFinite(sample.timestamp))
    .map((sample) => ({ ...sample, timestamp: capacityTimestamp(sample.timestamp) }))
    .sort((left, right) => left.timestamp - right.timestamp)
    .slice(-MAX_SANDBOX_CAPACITY_SAMPLES);
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
  const path = (key: "ready" | "target" | "creating") => visible.map((sample, index) => {
    const item = point(sample[key], index);
    return `${index === 0 ? "M" : "L"}${item.x.toFixed(1)},${item.y.toFixed(1)}`;
  }).join(" ");
  const latest = visible.at(-1);
  const latestPoint = latest ? point(latest.ready, visible.length - 1) : null;

  return <div className="sandbox-capacity-chart">
    <div className="sandbox-chart-legend"><span><i className="ready" />待命实例</span><span><i className="target" />目标容量</span><span><i className="creating" />创建中</span><small>{visible.length ? `最近 ${visible.length} 个采样` : "等待采样"}</small></div>
    {visible.length < 2 ? <div className="sandbox-chart-empty"><Activity size={18} />等待第二个采样点…</div> : <svg role="img" aria-label="Sandbox 容量实时趋势" viewBox={`0 0 ${width} ${height}`}>
      <title>Sandbox 容量实时趋势</title>
      <defs><linearGradient id="sandbox-ready-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#695bd7" stopOpacity=".22" /><stop offset="1" stopColor="#695bd7" stopOpacity="0" /></linearGradient></defs>
      {[0, 1, 2, 3, 4].map((line) => { const y = padding.top + chartHeight - line / 4 * chartHeight; return <line key={line} x1={padding.left} x2={width - padding.right} y1={y} y2={y} className="sandbox-chart-grid" />; })}
      <path d={`${path("ready")} L${padding.left + chartWidth},${padding.top + chartHeight} L${padding.left},${padding.top + chartHeight} Z`} className="sandbox-chart-area" />
      <path d={path("target")} className="sandbox-chart-line target" />
      <path d={path("creating")} className="sandbox-chart-line creating" />
      <path d={path("ready")} className="sandbox-chart-line ready" />
      {latestPoint && <><circle cx={latestPoint.x} cy={latestPoint.y} r="8" className="sandbox-chart-pulse" /><circle cx={latestPoint.x} cy={latestPoint.y} r="4" className="sandbox-chart-dot" /></>}
      <text x={padding.left} y={height - 8}>{dateTime(new Date(visible[0].timestamp * 1000).toISOString())}</text>
      <text x={width - padding.right} y={height - 8} textAnchor="end">{dateTime(new Date(visible.at(-1)!.timestamp * 1000).toISOString())}</text>
    </svg>}
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
  live,
  loading,
  logLoading,
  error = "",
  onRefresh,
  onDrain,
}: {
  overview: SandboxOverview | null;
  logs: SandboxLogEntry[];
  runtimes: SandboxRuntime[];
  executions: SandboxExecution[];
  live: boolean;
  loading: boolean;
  logLoading: boolean;
  error?: string;
  onRefresh: () => void;
  onDrain: (runtimeId: string) => void;
}) {
  const visibleStates = STATE_ORDER.map((state) => [state, overview?.runtime_states[state] ?? 0] as const);
  const visibleLogs = filterSandboxLogs(logs);
  return <div className="sandbox-monitor-page">
    <section className="sandbox-monitor-hero">
      <div><span className="sandbox-eyebrow">SANDBOX OPERATIONS</span><h2>代码沙箱监控</h2><p>集中查看预热池、运行时和执行健康度。页面只保留可操作的摘要，原始代码与标准输出不会进入监控流。</p></div>
      <div className="sandbox-monitor-hero-actions"><span className={`sandbox-live-status ${live ? "online" : "offline"}`}><i />{live ? "实时同步" : "等待连接"}</span><button type="button" onClick={onRefresh} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新</button></div>
    </section>
    {error && <div className="sandbox-monitor-error"><AlertTriangle size={17} /><span>{error}</span></div>}
    {loading && !overview ? <div className="sandbox-monitor-loading"><RefreshCw className="spin" /><span>正在读取沙箱运行状态…</span></div> : overview && <>
      <div className="sandbox-metric-grid">
        <MetricCard icon={Cpu} label="预热池" value={`${number(overview.capacity.ready)} / ${number(overview.capacity.target)}`} hint={overview.capacity.deficit ? `缺口 ${overview.capacity.deficit} 个` : "容量在目标范围内"} tone={overview.capacity.deficit ? "warning" : "success"} />
        <MetricCard icon={Activity} label="运行中" value={number(overview.active_executions)} hint={`到达率 ${number(overview.capacity.arrival_rate_per_min, " /min")}`} tone="accent" />
        <MetricCard icon={TimerReset} label="P95 执行耗时" value={number(overview.execution_latency.p95_ms, " ms")} hint={`P50 ${number(overview.execution_latency.p50_ms, " ms")}`} />
        <MetricCard icon={ShieldAlert} label="近期故障" value={number(overview.recent_failures)} hint={`${overview.execution_latency.sample_count} 个完成样本`} tone={overview.recent_failures ? "danger" : "success"} />
      </div>
      <section className="sandbox-monitor-panel sandbox-capacity-panel"><header><div><span className="sandbox-section-kicker">CAPACITY SIGNAL</span><h3>容量实时趋势</h3><p>客户端持续保留最近 60 个采样点，服务端短暂无历史时曲线也不会重置。</p></div><span className="sandbox-refresh-hint"><Wifi size={14} />每 2 秒同步 · {dateTime(overview.sampled_at)}</span></header><CapacityChart samples={overview.capacity_history} /></section>
      <div className="sandbox-monitor-columns">
        <section className="sandbox-monitor-panel sandbox-health-panel"><header><div><span className="sandbox-section-kicker">RUNTIME HEALTH</span><h3>运行时健康</h3><p>当前实例按生命周期状态聚合。</p></div><span className="sandbox-health-total"><TerminalSquare size={14} />{runtimes.length} 实例</span></header><div className="sandbox-state-grid">{visibleStates.map(([state, count]) => <div key={state}><span className={`sandbox-state-dot ${state}`} /><span>{STATE_LABELS[state] ?? state}</span><strong>{count}</strong></div>)}</div>{overview.alerts.length > 0 && <div className="sandbox-alert-list">{overview.alerts.map((alert) => <div key={alert.code} className={alert.severity === "critical" ? "critical" : "warning"}><AlertTriangle size={15} /><span><strong>{alert.message}</strong><small>{alert.severity === "critical" ? "需要立即处理" : "需要关注"}</small></span></div>)}</div>}{!overview.alerts.length && <div className="sandbox-all-clear"><CheckCircle2 size={17} /><span>当前没有容量告警</span></div>}</section>
        <section className="sandbox-monitor-panel sandbox-log-panel"><header><div><span className="sandbox-section-kicker">SIGNAL STREAM</span><h3>运行日志</h3><p>只显示异常和状态变化，自动清理 10 分钟以前的记录。</p></div><span className="sandbox-log-count">{logLoading ? "同步中…" : `${visibleLogs.length} 条`}</span></header><div className="sandbox-log-list" aria-live="polite">{visibleLogs.map((item) => <article key={item.id}><span className={`sandbox-log-level ${item.level}`}>{levelLabel(item.level)}</span><div><strong>{item.message}</strong><small>{dateTime(item.timestamp)} · {item.event_type}{item.runtime_id ? ` · ${item.runtime_id.slice(0, 10)}` : ""}</small></div></article>)}{!visibleLogs.length && <EmptyState text="暂无需要关注的运行日志" />}</div></section>
      </div>
      <div className="sandbox-monitor-columns lower">
        <section className="sandbox-monitor-panel sandbox-inventory-panel"><header><div><span className="sandbox-section-kicker">RUNTIME INVENTORY</span><h3>运行时实例</h3></div></header><div className="sandbox-runtime-list">{runtimes.slice(0, 12).map((runtime) => <article key={runtime.id}><span className={`sandbox-state-dot ${runtime.state}`} /><div><strong>{runtime.id.slice(0, 16)}</strong><small>{STATE_LABELS[runtime.state] ?? runtime.state} · {runtime.node_id ?? "未绑定节点"}</small></div>{["assigned", "ready_unbound", "claiming"].includes(runtime.state) && <button type="button" onClick={() => onDrain(runtime.id)}>排空</button>}</article>)}{!runtimes.length && <EmptyState text="暂无运行时实例" />}</div></section>
        <section className="sandbox-monitor-panel sandbox-execution-panel"><header><div><span className="sandbox-section-kicker">EXECUTION QUEUE</span><h3>最近执行</h3></div></header><div className="sandbox-execution-list">{executions.slice(0, 12).map((execution) => <article key={execution.id}><span className={`sandbox-execution-status ${execution.status}`} /> <div><strong>{execution.id.slice(0, 16)}</strong><small>{execution.status} · {dateTime(execution.started_at)}</small></div><span className="sandbox-execution-runtime">{execution.runtime_instance_id?.slice(0, 10) ?? "—"}</span></article>)}{!executions.length && <EmptyState text="暂无执行记录" />}</div></section>
      </div>
    </>}
  </div>;
}
