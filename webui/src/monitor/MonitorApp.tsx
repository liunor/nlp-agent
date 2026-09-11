import { Activity, AlertTriangle, Bot, Clock3, Database, Gauge, HardDrive, Layers3, MoreHorizontal, Radio, RefreshCw, Search, Server, ShieldCheck, TerminalSquare, Timer, Trash2, X, Zap } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ConfirmDialog } from "@/shared/ui/ConfirmDialog";
import { authenticate, monitorApi, type MonitorSession, type Overview, type SystemUsageDimension, type SystemUsageSnapshot, type TelemetryEvent, type Trace, type TraceDetail, type UsageRow } from "./api";
import { controlPlaneUrl, monitorPageFromLocation, monitorPathForPage, resetMonitorData, safeEventContext, telemetryFrame, type MonitorPage, type TraceChain } from "./monitor-helpers";
import { mergeSandboxCapacitySamples, mergeSandboxLogs, SANDBOX_REFRESH_INTERVAL_MS, SandboxMonitorPage, type SandboxExecution, type SandboxLogEntry, type SandboxOverview, type SandboxRuntime } from "./SandboxMonitorPage";
import { AuthorizationAuditPage } from "./AuthorizationAuditPage";
import { MonitorLoginPage } from "./MonitorLoginPage";
import { MonitorComponentsPage, MonitorErrorsPage, MonitorOverviewPage, MonitorStoragePage, MonitorUsagePage } from "./MonitorDashboardPages";
import { TraceExplorerPage } from "./TraceExplorerPage";

const LiveDiagnosticsPage = lazy(() => import("./LiveDiagnosticsPage").then(({ LiveDiagnosticsPage: Page }) => ({ default: Page })));

type Page = MonitorPage;
const NAV: Array<{ page: Page; label: string; icon: typeof Gauge }> = [
  { page: "overview", label: "系统总览", icon: Gauge },
  { page: "usage", label: "用量中心", icon: Database },
  { page: "traces", label: "运行链路", icon: Activity },
  { page: "components", label: "组件与模型", icon: Layers3 },
  { page: "errors", label: "错误分析", icon: AlertTriangle },
  { page: "events", label: "实时诊断", icon: Radio },
  { page: "sandbox", label: "代码沙箱", icon: TerminalSquare },
  { page: "audit", label: "审计日志", icon: ShieldCheck },
  { page: "storage", label: "数据留存", icon: HardDrive },
];
const NAV_GROUPS: Array<{ label: string; items: typeof NAV }> = [
  { label: "概览", items: NAV.filter((item) => item.page === "overview") },
  { label: "请求洞察", items: NAV.filter((item) => ["usage", "traces", "components", "errors"].includes(item.page)) },
  { label: "运行环境", items: NAV.filter((item) => ["events", "sandbox"].includes(item.page)) },
  { label: "治理", items: NAV.filter((item) => ["audit", "storage"].includes(item.page)) },
];
function fmt(value: number | null | undefined, suffix = "") { return value == null ? "—" : `${value.toLocaleString()}${suffix}`; }
function time(value?: string) { return value ? new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value)) : "—"; }
function storageRetentionDays(storage: Record<string, unknown>) {
  const retention = storage.retention;
  if (typeof retention !== "object" || retention === null) return 30;
  const days = Number((retention as { trace_days?: unknown }).trace_days);
  return Number.isFinite(days) && days > 0 ? days : 30;
}
function Status({ value }: { value: string }) { return <span className={`mon-status ${value}`}>{value}</span>; }
function Empty({ text }: { text: string }) { return <div className="mon-empty"><Database /><span>{text}</span></div>; }
function authStatus(reason: unknown): number | undefined {
  if (typeof reason !== "object" || reason === null || !("status" in reason)) return undefined;
  const status = (reason as { status?: unknown }).status;
  return typeof status === "number" ? status : undefined;
}
function authMessage(reason: unknown): string {
  const status = authStatus(reason);
  if (status === 401) return "登录监控平台后才能查看运行数据。";
  if (status === 403) return "当前账号没有监控权限，请联系管理员授权。";
  return reason instanceof Error ? reason.message : "监控平台认证失败，请稍后重试。";
}

const TOKEN_LABELS: Record<string, string> = {
  input_tokens: "输入 Token",
  cached_input_tokens: "缓存读取",
  cache_miss_input_tokens: "缓存未命中",
  cache_write_input_tokens: "缓存写入",
  output_tokens: "输出 Token",
  reasoning_output_tokens: "推理 Token",
  total_tokens: "总 Token",
  cached_tokens: "缓存 Token",
  cache_miss_tokens: "缓存未命中",
  reasoning_tokens: "推理 Token",
};

function tokenLabel(key: string) { return TOKEN_LABELS[key] ?? key.replaceAll("_", " "); }
function credits(value: number | null | undefined) { return value == null ? "未完整计价" : `${(value / 1_000_000).toFixed(4)} credits`; }

function DimensionChips({ title, items }: { title: string; items: Array<{ value: string; requests: number }> }) {
  return <section className="mon-overview-mini-panel"><header><h3>{title}</h3><span>{items.reduce((sum, item) => sum + item.requests, 0)} 次</span></header>{items.length ? <div className="mon-dimension-list">{items.slice(0, 8).map((item) => <div key={item.value}><code>{item.value}</code><b>{fmt(item.requests)}</b></div>)}</div> : <Empty text="暂无标记" />}</section>;
}

function UsageDimensionTable({ title, description, rows, label }: { title: string; description: string; rows: SystemUsageDimension[]; label: (row: SystemUsageDimension) => string }) {
  return <section className="mon-panel mon-usage-dimension-panel"><header><div><h2>{title}</h2><p>{description}</p></div></header>{rows.length ? <div className="mon-table"><table><thead><tr><th>维度</th><th>调用</th><th>计价</th><th>总 Token</th><th>Credits</th></tr></thead><tbody>{rows.slice(0, 8).map((row, index) => <tr key={`${label(row)}-${index}`}><td><strong>{label(row)}</strong>{row.provider ? <small>{row.provider}</small> : null}</td><td>{fmt(row.events)}</td><td>{row.credits_complete ? `${fmt(row.priced_events)} 完整` : `${fmt(row.unpriced_events)} 待补`}</td><td>{fmt(row.tokens.total_tokens)}</td><td>{credits(row.credits_micro)}</td></tr>)}</tbody></table></div> : <Empty text="暂无详细用量" />}</section>;
}

function usageTotalTokens(row: { tokens?: Record<string, number>; total_tokens?: number }) {
  return Number(row.tokens?.total_tokens ?? row.total_tokens ?? 0);
}

export function LegacyOverviewPage({ data, usage, systemUsage }: { data: Overview; usage: UsageRow[]; systemUsage: SystemUsageSnapshot | null }) {
  const tags = data.tags ?? { channels: [], sources: [], span_kinds: [] };
  const statusBreakdown = data.status_breakdown ?? [];
  const componentSpans = data.component_spans ?? [];
  const models = data.models ?? [];
  const errorGroups = data.error_groups ?? [];
  const topUsers = data.top_users ?? [];
  const tokenValues = systemUsage && Object.keys(systemUsage.tokens).length ? systemUsage.tokens : data.tokens ?? {};
  const breakdown = systemUsage?.breakdown?.length ? systemUsage.breakdown : usage.map((row) => ({ ...row, tokens: { total_tokens: row.total_tokens } }));
  const max = Math.max(1, ...breakdown.map(usageTotalTokens));
  const runtime = data.runtime ?? {};
  const queueSize = typeof runtime.queue_size === "number" ? runtime.queue_size : null;
  const queueCapacity = typeof runtime.queue_capacity === "number" ? runtime.queue_capacity : null;
  const queueRatio = queueSize != null && queueCapacity ? queueSize / queueCapacity : null;
  const detailedUsers = systemUsage?.users ?? [];
  const detailedModels = systemUsage?.models ?? [];
  return <div className="mon-stack">
    <section className="mon-overview-hero"><div><span className="mon-section-kicker">SYSTEM OBSERVABILITY · ALL USERS</span><h2>所有用户运行情况</h2><p>以逻辑请求为主口径，汇总所有用户、工作区和会话；内部 Model / Worker / Tool attempt 单独展示，便于定位重试、依赖和容量问题。</p></div><div className="mon-overview-hero-meta"><span>统计周期</span><strong>最近 {data.period_days} 天</strong><small>{data.from ? time(data.from) : "实时查询"} — {data.to ? time(data.to) : "现在"}</small></div></section>
    <div className="mon-kpis mon-kpis-wide">
      <article><Zap /><span>逻辑请求</span><strong>{fmt(data.requests)}</strong><small>{fmt(data.successes)} 成功 · {fmt(data.failed_requests ?? data.errors)} 失败</small></article>
      <article><Activity /><span>活跃用户</span><strong>{fmt(data.active_users)}</strong><small>{fmt(data.active_workspaces)} 个工作区</small></article>
      <article><Bot /><span>活跃会话</span><strong>{fmt(data.active_sessions)}</strong><small>全用户合计</small></article>
      <article><Timer /><span>P95 响应</span><strong>{fmt(data.latency_ms.p95, " ms")}</strong><small>P50 {fmt(data.latency_ms.p50, " ms")}</small></article>
      <article><Clock3 /><span>P95 首 Token</span><strong>{fmt(data.ttft_ms.p95, " ms")}</strong><small>P50 {fmt(data.ttft_ms.p50, " ms")}</small></article>
      <article className={data.error_rate > .05 ? "danger" : "success"}><AlertTriangle /><span>逻辑错误率</span><strong>{(data.error_rate * 100).toFixed(2)}%</strong><small>{fmt(data.errors)} error / timeout</small></article>
    </div>
    <div className="mon-overview-grid mon-overview-main-grid">
      <section className="mon-panel"><header><div><span className="mon-overview-panel-kicker">全局用量账本</span><h2>Token 与缓存</h2><p>来自详细 UsageEvent 账本：所有用户的 canonical Token、计价完整度和 Credits。</p></div><span className={`mon-coverage-badge ${systemUsage?.credits_complete === false ? "warning" : ""}`}>{systemUsage ? (systemUsage.credits_complete ? "计价完整" : "部分待补") : "账本未连接"}</span></header><div className="mon-token-grid mon-token-grid-detailed">{Object.entries(tokenValues).map(([key, value]) => <article key={key}><span>{tokenLabel(key)}</span><strong>{fmt(value)}</strong></article>)}</div><div className="mon-usage-ledger-footer"><span>{systemUsage ? `${fmt(systemUsage.events)} 次用量事件 · ${fmt(systemUsage.priced_events)} 次已计价` : "当前显示观测 Trace 的兼容 Token 汇总"}</span><strong>{credits(systemUsage?.credits_micro)}</strong></div></section>
      <section className="mon-panel mon-operational-panel"><header><div><h2>服务与采集健康</h2><p>传统后端监控中的饱和、可用性和遥测管道信号。</p></div></header><div className="mon-operational-grid"><div><span>Telemetry 队列</span><strong>{queueSize == null ? "—" : `${fmt(queueSize)} / ${fmt(queueCapacity)}`}</strong><small>{queueRatio == null ? "容量未知" : `${(queueRatio * 100).toFixed(1)}% 占用`}</small></div><div><span>进行中 Trace</span><strong>{fmt(typeof runtime.active_traces === "number" ? runtime.active_traces : null)}</strong><small>当前活跃请求</small></div><div><span>丢弃事件</span><strong className={Number(runtime.dropped_events ?? 0) > 0 ? "danger-text" : ""}>{fmt(typeof runtime.dropped_events === "number" ? runtime.dropped_events : null)}</strong><small>非 debug 事件应重点关注</small></div><div><span>实时订阅</span><strong>{fmt(typeof runtime.live_subscribers === "number" ? runtime.live_subscribers : null)}</strong><small>Monitor WebSocket</small></div><div><span>结果状态</span><strong>{statusBreakdown.length ? statusBreakdown.map((item) => `${item.value} ${item.requests}`).join(" · ") : "—"}</strong><small>{fmt(data.requests)} 条逻辑请求</small></div></div></section>
    </div>
    <section className="mon-panel"><header><div><h2>请求标记与流量切片</h2><p>把已有 Trace / Span / Event 标记展开，先发现入口、来源、组件和状态异常，再下钻到 Trace。</p></div></header><div className="mon-overview-dimensions"><DimensionChips title="Channel" items={tags.channels} /><DimensionChips title="Source" items={tags.sources} /><DimensionChips title="Span kind" items={tags.span_kinds} /><DimensionChips title="Event level" items={Object.entries(data.events_by_level ?? {}).map(([value, requests]) => ({ value, requests }))} /></div></section>
    <div className="mon-overview-grid">
      <section className="mon-panel mon-component-panel"><header><div><h2>组件与依赖健康</h2><p>Span attempt 口径；看出 Model / Worker / Tool 的调用量、失败、重试和平均耗时。</p></div></header>{componentSpans.length ? <div className="mon-table"><table><thead><tr><th>组件</th><th>调用</th><th>失败率</th><th>重试</th><th>平均耗时</th><th>Token</th></tr></thead><tbody>{componentSpans.slice(0, 12).map((row) => <tr key={`${row.label}-${row.name}`}><td><strong>{row.name}</strong><small>{row.label}</small></td><td>{fmt(row.requests)}</td><td className={row.error_rate > .05 ? "danger-text" : ""}>{(row.error_rate * 100).toFixed(1)}%</td><td>{fmt(row.retries)}</td><td>{fmt(row.avg_duration_ms, " ms")}</td><td>{fmt(row.total_tokens)}</td></tr>)}</tbody></table></div> : <Empty text="当前周期没有组件 Span" />}</section>
      <section className="mon-panel mon-component-panel"><header><div><h2>模型与 Provider</h2><p>模型调用、失败、重试和 Token，帮助识别单一 Provider 回归。</p></div></header>{models.length ? <div className="mon-table"><table><thead><tr><th>模型</th><th>调用</th><th>失败率</th><th>重试</th><th>平均耗时</th><th>Token</th></tr></thead><tbody>{models.slice(0, 12).map((row) => <tr key={`${row.provider}-${row.provider_model}-${row.model_profile}`}><td><strong>{row.provider_model}</strong><small>{row.provider} · {row.model_profile}</small></td><td>{fmt(row.requests)}</td><td className={row.error_rate > .05 ? "danger-text" : ""}>{(row.error_rate * 100).toFixed(1)}%</td><td>{fmt(row.retries)}</td><td>{fmt(row.avg_duration_ms, " ms")}</td><td>{fmt(row.total_tokens)}</td></tr>)}</tbody></table></div> : <Empty text="当前周期没有模型 Span" />}</section>
    </div>
    <div className="mon-overview-grid">
      <section className="mon-panel"><header><div><h2>用户与工作区热度</h2><p>按逻辑请求聚合，不把用户 ID 作为指标标签；这里用于监控下钻。</p></div></header>{topUsers.length ? <div className="mon-table"><table><thead><tr><th>用户</th><th>请求</th><th>错误率</th><th>平均响应</th><th>Token</th><th>工作区</th><th>最后活跃</th></tr></thead><tbody>{topUsers.slice(0, 12).map((row) => <tr key={row.user_id}><td><strong>{row.user_id}</strong></td><td>{fmt(row.requests)}</td><td>{(row.error_rate * 100).toFixed(1)}%</td><td>{fmt(row.avg_duration_ms, " ms")}</td><td>{fmt(row.total_tokens)}</td><td>{row.workspaces.join(" · ")}</td><td>{time(row.last_seen ?? undefined)}</td></tr>)}</tbody></table></div> : <Empty text="当前周期没有用户请求" />}</section>
      <div className="mon-detail-usage-stack"><UsageDimensionTable title="详细用量分布" description="按用户汇总详细 Token / Credits。" rows={detailedUsers} label={(row) => row.user_id ?? "unknown"} /><UsageDimensionTable title="按 Provider" description="计价事件和 Token 的 Provider 分布。" rows={systemUsage?.providers ?? []} label={(row) => row.provider ?? "unknown"} /><UsageDimensionTable title="按模型" description="Provider model 的详细账本分布。" rows={detailedModels} label={(row) => row.provider_model ?? "unknown"} /></div>
    </div>
    <div className="mon-overview-grid">
      <section className="mon-panel"><header><div><h2>错误分组</h2><p>按 error kind / 组件 / 操作名聚合，避免只看到一个总错误率。</p></div></header>{errorGroups.length ? <div className="mon-error-grid">{errorGroups.slice(0, 8).map((row) => <div className="mon-error-summary" key={`${row.error_kind}-${row.kind}-${row.name}`}><AlertTriangle /><span><strong>{row.error_kind}</strong><small>{row.kind} · {row.name}</small></span><b>{row.count}</b><time>{time(row.last_seen ?? undefined)}</time></div>)}</div> : <Empty text="当前周期没有组件错误" />}</section>
      <section className="mon-panel"><header><div><h2>用量趋势</h2><p>优先使用详细账本的日 / 周聚合，展示全用户真实用量。</p></div></header>{breakdown.length ? <div className="mon-bars">{breakdown.slice(-24).map((row, index) => { const total = usageTotalTokens(row); return <div key={`${row.day}-${index}`} title={`${row.day}: ${total}`}><span style={{ height: `${Math.max(3, total / max * 100)}%` }} /><small>{row.day.slice(5)}</small></div>; })}</div> : <Empty text="还没有 Token 用量数据" />}</section>
    </div>
    <div className="mon-overview-footnote">数据范围：逻辑请求来自 Telemetry Trace；详细 Token / Credits 来自 UsageEvent。取消、拒绝和重试会分别保留在状态或 Span attempt 中，避免把内部重试误算成用户请求。</div>
  </div>;
}

export function RunList({ chains, onOpen }: { chains: TraceChain[]; onOpen: (chain: TraceChain) => void }) {
  const [query, setQuery] = useState("");
  const visible = chains.filter((chain) => `${chain.sessionId} ${chain.turnId} ${chain.traces.map((trace) => `${trace.trace_id} ${trace.status} ${trace.source}`).join(" ")}`.toLowerCase().includes(query.toLowerCase()));
  return <section className="mon-panel"><header><div><h2>运行链路</h2><p>一次评测运行作为一个抽屉；普通对话按 Turn 收纳，内部再展示对应的 Trace。</p></div><label className="mon-search"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="评测 / Case / Trace / Session" /></label></header><div className="mon-run-list">{visible.map((chain) => { const latest = chain.traces.at(-1)!; const totalTokens = chain.traces.reduce((sum, trace) => sum + trace.total_tokens, 0); const cases = new Set(chain.traces.map((trace) => trace.attributes.evaluation_case_id).filter((value): value is string => typeof value === "string")); return <button className={`mon-run-card mon-chain-card ${chain.evaluationRunId ? "mon-evaluation-card" : ""}`} type="button" key={chain.key} onClick={() => onOpen(chain)}><time>{time(chain.traces[0]?.started_at)}</time><div className="mon-run-title">{chain.evaluationRunId ? <><span>评测批次</span><strong>运行 <code>{chain.evaluationRunId.slice(0, 12)}</code></strong><small>{chain.evaluationSuiteId ?? "evaluation"} · {cases.size} 个 Case · {chain.traces.length} 条 Trace</small></> : <><strong>对话链路 <code>{chain.turnId.slice(0, 12)}</code></strong><small>session {chain.sessionId.slice(0, 12)} · {chain.traces.length} 条 Trace</small></>}</div><Status value={latest.status} /><dl><div><dt>{chain.evaluationRunId ? "Case" : "Trace"}</dt><dd>{chain.evaluationRunId ? cases.size : chain.traces.length}</dd></div><div><dt>Trace</dt><dd>{chain.traces.length} 条</dd></div><div><dt>累计 Token</dt><dd>{fmt(totalTokens)}</dd></div><div><dt>{chain.evaluationRunId ? "Suite" : "会话"}</dt><dd><code>{(chain.evaluationSuiteId ?? chain.sessionId).slice(0, 12)}</code></dd></div></dl></button>; })}{!visible.length && <Empty text="没有匹配的运行链路" />}</div></section>;
}

function TraceDrawer({ detail, onClose, safeMode = false }: { detail: TraceDetail; onClose: () => void; safeMode?: boolean }) { const start = new Date(detail.trace.started_at).getTime(); const total = Math.max(detail.trace.duration_ms ?? 1, 1); return <><button className="mon-drawer-backdrop" type="button" onClick={onClose} aria-label="关闭" /><aside className="mon-drawer"><header><div><span>TRACE DETAIL</span><h2>{detail.trace.trace_id}</h2></div><button type="button" onClick={onClose}><X /></button></header><div className="mon-drawer-body"><div className="mon-detail-grid"><article><span>状态</span><Status value={detail.trace.status} /></article><article><span>总耗时</span><strong>{fmt(detail.trace.duration_ms, " ms")}</strong></article><article><span>首 Token</span><strong>{fmt(detail.trace.ttft_ms, " ms")}</strong></article><article><span>总 Token</span><strong>{fmt(detail.trace.total_tokens)}</strong></article></div>{detail.detail_limits?.children_truncated ? <div className="mon-inline-warning"><AlertTriangle size={14} />子 Span 或事件超过单次详情上限 {fmt(detail.detail_limits.children)}，请缩小链路范围。</div> : null}<section><h3>Coordinator / Worker / Tool 时间线</h3><div className="mon-timeline">{detail.spans.map((span) => { const offset = Math.max(0, new Date(span.started_at).getTime() - start); return <article key={span.span_id}><div><strong>{span.name}</strong><span>{span.kind} · {span.worker_id ?? "coordinator"}</span></div><Status value={span.status} /><div className="mon-track"><i style={{ marginLeft: `${offset / total * 100}%`, width: `${Math.max(1, (span.duration_ms ?? 1) / total * 100)}%` }} /></div><small>{fmt(span.duration_ms, " ms")} · attempt {span.attempt} · {fmt(span.total_tokens)} tokens</small></article>; })}</div></section><section><h3>事件</h3><div className="mon-event-list">{detail.events.map((event) => <article key={event.event_id}><time>{time(event.timestamp)}</time><Status value={event.level} /><strong>{event.name}</strong>{safeMode ? <p className="mon-safe-event-context">{safeEventContext(event).join(" · ") || "已隐藏原始 payload，仅保留诊断上下文"}</p> : <pre>{JSON.stringify(event.payload, null, 2)}</pre>}</article>)}</div></section>{safeMode ? <p className="mon-privacy-note">此 Trace 从实时诊断打开，已隐藏原始事件和请求内容。</p> : <details><summary>原始 Trace / Tool JSON</summary><pre className="mon-json">{JSON.stringify(detail, null, 2)}</pre></details>}</div></aside></>; }

function ChainDrawer({ chain, onClose, onOpenTrace }: { chain: TraceChain; onClose: () => void; onOpenTrace: (trace: Trace) => void }) {
  return <><button className="mon-drawer-backdrop" type="button" onClick={onClose} aria-label="关闭运行链路" /><aside className="mon-drawer mon-chain-drawer"><header><div><span>{chain.evaluationRunId ? "EVALUATION RUN" : "RUN CHAIN"}</span><h2>{chain.evaluationRunId ? `运行 ${chain.evaluationRunId}` : `turn ${chain.turnId}`}</h2><small>{chain.evaluationRunId ? `${chain.evaluationSuiteId ?? "evaluation"} · 共 ${new Set(chain.traces.map((trace) => trace.attributes.evaluation_case_id)).size} 个 Case / ${chain.traces.length} 条 Trace` : `session ${chain.sessionId} · 共 ${chain.traces.length} 条 Trace`}</small></div><button type="button" onClick={onClose} aria-label="关闭"><X /></button></header><div className="mon-drawer-body"><section className="mon-chain-intro"><h3>{chain.evaluationRunId ? "本次评测的 Trace" : "本次链路的 Trace"}</h3><p>{chain.evaluationRunId ? "每个 Case 使用独立 Session，但全部属于这一次评测运行。点击卡片可查看 Worker、工具和完整 Trace。" : "按产生顺序排列。先查看入口 Trace，再检查 Worker 续接或后台恢复产生的后续 Trace。"}</p></section><div className="mon-chain-traces">{chain.traces.map((trace, index) => <button type="button" className="mon-chain-trace-card" key={trace.trace_id} onClick={() => onOpenTrace(trace)}><span className="mon-chain-sequence">{index + 1}</span><div><strong>{typeof trace.attributes.evaluation_case_id === "string" ? trace.attributes.evaluation_case_id : trace.source}</strong><small>{time(trace.started_at)} · trace {trace.trace_id.slice(0, 12)}</small></div><Status value={trace.status} /><dl><div><dt>响应</dt><dd>{fmt(trace.duration_ms, " ms")}</dd></div><div><dt>Token</dt><dd>{fmt(trace.total_tokens)}</dd></div><div><dt>首 Token</dt><dd>{fmt(trace.ttft_ms, " ms")}</dd></div></dl></button>)}</div></div></aside></>;
}

export function MonitorApp() {
  const [page, setPage] = useState<Page>(() => monitorPageFromLocation());
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);
  const [authState, setAuthState] = useState<"checking" | "authenticated" | "login">("checking");
  const [monitorSession, setMonitorSession] = useState<MonitorSession | null>(null);
  const [authMessageText, setAuthMessageText] = useState("");
  const [error, setError] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [systemUsage, setSystemUsage] = useState<SystemUsageSnapshot | null>(null);
  const [usage, setUsage] = useState<UsageRow[]>([]);
  const [events, setEvents] = useState<TelemetryEvent[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [eventsError, setEventsError] = useState("");
  const liveEventIds = useRef(new Set<string>());
  const [storage, setStorage] = useState<Record<string, unknown>>({});
  const [sandboxOverview, setSandboxOverview] = useState<SandboxOverview | null>(null);
  const [sandboxRuntimes, setSandboxRuntimes] = useState<SandboxRuntime[]>([]);
  const [sandboxExecutions, setSandboxExecutions] = useState<SandboxExecution[]>([]);
  const [sandboxRuntimePage, setSandboxRuntimePage] = useState({ total: 0, has_more: false });
  const [sandboxExecutionPage, setSandboxExecutionPage] = useState({ total: 0, has_more: false });
  const [sandboxLogs, setSandboxLogs] = useState<SandboxLogEntry[]>([]);
  const [sandboxLoading, setSandboxLoading] = useState(false);
  const [sandboxLogLoading, setSandboxLogLoading] = useState(false);
  const [sandboxListLoading, setSandboxListLoading] = useState(false);
  const [sandboxError, setSandboxError] = useState("");
  const [sandboxLive, setSandboxLive] = useState(false);
  const sandboxRefreshInFlight = useRef(false);
  const [chain, setChain] = useState<TraceChain | null>(null);
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [detailSafeMode, setDetailSafeMode] = useState(false);
  const [live, setLive] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [dangerOpen, setDangerOpen] = useState(false);
  const [resetting, setResetting] = useState(false);
  const load = useCallback(async () => { setLoading(true); setError(""); try { const session = await authenticate(); setMonitorSession(session); setAuthState("authenticated"); const [overviewResult, usageResult, storageResult] = await Promise.all([monitorApi.overview(days), monitorApi.usage(days), monitorApi.storage()]); const systemUsageResult = await monitorApi.systemUsage(days, false).catch((reason) => { if (authStatus(reason) === 401 || authStatus(reason) === 403) throw reason; return null; }); setOverview(overviewResult); setSystemUsage(systemUsageResult); setUsage(usageResult.items); setStorage(storageResult); } catch (reason) { if (authStatus(reason) === 401 || authStatus(reason) === 403) { setMonitorSession(null); setAuthState("login"); setAuthMessageText(authMessage(reason)); } else { setAuthState("authenticated"); setError(reason instanceof Error ? reason.message : String(reason)); } } finally { setLoading(false); } }, [days]);
  const loadEvents = useCallback(async () => { setEventsLoading(true); setEventsError(""); try { const result = await monitorApi.events(100); setEvents((current) => { const merged = new Map(result.items.map((item) => [item.event_id, item])); for (const item of current) if (liveEventIds.current.has(item.event_id)) merged.set(item.event_id, item); return [...merged.values()].sort((left, right) => Date.parse(right.timestamp) - Date.parse(left.timestamp)).slice(0, 100); }); } catch (reason) { if (authStatus(reason) === 401 || authStatus(reason) === 403) { setAuthState("login"); setAuthMessageText(authMessage(reason)); } else { setEventsError(reason instanceof Error ? reason.message : String(reason)); } } finally { setEventsLoading(false); } }, []);
  const login = useCallback(async (username: string, password: string) => { setAuthMessageText(""); try { await monitorApi.login(username, password); setAuthState("checking"); await load(); } catch (reason) { setAuthState("login"); setAuthMessageText(authMessage(reason)); throw reason; } }, [load]);
  const handleAuthFailure = useCallback((reason: unknown) => { setAuthState("login"); setAuthMessageText(authMessage(reason)); }, []);
  const loadSandbox = useCallback(async (initial = false) => {
    if (sandboxRefreshInFlight.current) return;
    sandboxRefreshInFlight.current = true;
    if (initial) setSandboxLoading(true);
    else setSandboxLogLoading(true);
    setSandboxError("");
    try {
      const [nextOverview, nextRuntimes, nextExecutions, nextLogs] = await Promise.all([
        monitorApi.sandboxOverview(), monitorApi.sandboxRuntimes(), monitorApi.sandboxExecutions(), monitorApi.sandboxLogs(),
      ]);
      setSandboxOverview((current) => ({
        ...nextOverview,
        capacity_history: mergeSandboxCapacitySamples(current?.capacity_history ?? [], nextOverview.capacity_history),
      }));
      setSandboxRuntimes((current) => initial
        ? nextRuntimes.items
        : [...nextRuntimes.items, ...current.filter((item) => !nextRuntimes.items.some((incoming) => incoming.id === item.id))]);
      setSandboxExecutions((current) => initial
        ? nextExecutions.items
        : [...nextExecutions.items, ...current.filter((item) => !nextExecutions.items.some((incoming) => incoming.id === item.id))]);
      setSandboxRuntimePage({ total: nextRuntimes.total, has_more: nextRuntimes.has_more });
      setSandboxExecutionPage({ total: nextExecutions.total, has_more: nextExecutions.has_more });
      setSandboxLogs((current) => mergeSandboxLogs(current, nextLogs.items));
      setSandboxLive(true);
    } catch (reason) {
      setSandboxError(reason instanceof Error ? reason.message : String(reason));
      setSandboxLive(false);
    } finally {
      sandboxRefreshInFlight.current = false;
      if (initial) setSandboxLoading(false);
      else setSandboxLogLoading(false);
    }
  }, []);
  const loadSandboxMore = useCallback(async (kind: "runtimes" | "executions") => {
    if (sandboxRefreshInFlight.current) return;
    const isRuntimes = kind === "runtimes";
    const offset = isRuntimes ? sandboxRuntimes.length : sandboxExecutions.length;
    const hasMore = isRuntimes ? sandboxRuntimePage.has_more : sandboxExecutionPage.has_more;
    if (!hasMore) return;
    sandboxRefreshInFlight.current = true;
    setSandboxListLoading(true);
    try {
      if (isRuntimes) {
        const page = await monitorApi.sandboxRuntimes(12, offset);
        setSandboxRuntimes((current) => {
          const merged = new Map(current.map((item) => [item.id, item]));
          for (const item of page.items) merged.set(item.id, item);
          return [...merged.values()];
        });
        setSandboxRuntimePage({ total: page.total, has_more: page.has_more });
      } else {
        const page = await monitorApi.sandboxExecutions(undefined, 12, offset);
        setSandboxExecutions((current) => {
          const merged = new Map(current.map((item) => [item.id, item]));
          for (const item of page.items) merged.set(item.id, item);
          return [...merged.values()];
        });
        setSandboxExecutionPage({ total: page.total, has_more: page.has_more });
      }
    } catch (reason) {
      setSandboxError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      sandboxRefreshInFlight.current = false;
      setSandboxListLoading(false);
    }
  }, [sandboxExecutionPage.has_more, sandboxExecutions.length, sandboxRuntimePage.has_more, sandboxRuntimes.length]);
  useEffect(() => { queueMicrotask(() => void load()); }, [load]);
  useEffect(() => { const onPopState = () => setPage(monitorPageFromLocation()); addEventListener("popstate", onPopState); return () => removeEventListener("popstate", onPopState); }, []);
  const hasOverview = overview !== null;
  useEffect(() => { if (!hasOverview || page !== "events") return undefined; queueMicrotask(() => void loadEvents()); const refreshTimer = window.setInterval(() => { if (document.visibilityState === "visible") void loadEvents(); }, 5 * 60 * 1000); return () => window.clearInterval(refreshTimer); }, [hasOverview, loadEvents, page]);
  useEffect(() => {
    if (page !== "sandbox") return undefined;
    queueMicrotask(() => void loadSandbox(true));
    const refreshTimer = window.setInterval(() => {
      if (document.visibilityState === "visible") void loadSandbox(false);
    }, SANDBOX_REFRESH_INTERVAL_MS);
    const cleanupTimer = window.setInterval(() => {
      setSandboxLogs((current) => mergeSandboxLogs(current, []));
    }, 60_000);
    return () => { window.clearInterval(refreshTimer); window.clearInterval(cleanupTimer); };
  }, [loadSandbox, page]);
  useEffect(() => { if (!hasOverview || page !== "events") return undefined; let socket: WebSocket | null = null; let cancelled = false; void monitorApi.createWsTicket().then(({ ticket }) => { if (cancelled) return; const protocol = location.protocol === "https:" ? "wss:" : "ws:"; socket = new WebSocket(`${protocol}//${location.host}/ws/observability?ticket=${encodeURIComponent(ticket)}`); socket.onopen = () => setLive(true); socket.onclose = () => setLive(false); socket.onmessage = (message) => { const frame = telemetryFrame(message.data); if (frame) { liveEventIds.current.add(frame.payload.event_id); setEvents((current) => [frame.payload, ...current.filter((item) => item.event_id !== frame.payload.event_id)].slice(0, 100)); } }; }).catch(() => setLive(false)); return () => { cancelled = true; socket?.close(); }; }, [hasOverview, page]);
  const openTrace = useCallback(async (trace: Trace) => {
    try {
      setDetailSafeMode(false);
      setDetail(await monitorApi.trace(trace.trace_id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, []);
  const openTraceById = useCallback(async (traceId: string) => {
    try {
      setDetailSafeMode(true);
      setDetail(await monitorApi.trace(traceId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, []);
  const resetAll = useCallback(async () => { setResetting(true); setError(""); try { await resetMonitorData(monitorApi.reset, load); liveEventIds.current.clear(); setEvents([]); setEventsError(""); setDetail(null); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); } finally { setResetting(false); setResetOpen(false); setDangerOpen(false); } }, [load]);
  const drainSandbox = useCallback(async (runtimeId: string) => {
    if (!confirm("确认排空这个运行时？")) return;
    try {
      await monitorApi.drainSandboxRuntime(runtimeId);
      await loadSandbox(false);
    } catch (reason) {
      setSandboxError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [loadSandbox]);
  const navigate = (next: Page) => { const path = monitorPathForPage(next, location); history.pushState({}, "", `${path}${location.hash}`); setPage(next); };
  const navigateToTraceFilter = useCallback((query: string) => { const path = monitorPathForPage("traces", location); const params = new URLSearchParams({ focus: "errors", query }); history.pushState({}, "", `${path}?${params.toString()}${location.hash}`); setPage("traces"); }, []);
  const traceSearch = new URLSearchParams(location.search);
  const traceFocus = traceSearch.get("focus") === "errors" || traceSearch.get("focus") === "slow" ? traceSearch.get("focus") as "errors" | "slow" : "all";
  const traceQuery = traceSearch.get("query") ?? "";
  const pageContent = useMemo(() => {
    if (page === "sandbox") return <SandboxMonitorPage overview={sandboxOverview} logs={sandboxLogs} runtimes={sandboxRuntimes} executions={sandboxExecutions} runtimeTotal={sandboxRuntimePage.total} executionTotal={sandboxExecutionPage.total} runtimeHasMore={sandboxRuntimePage.has_more} executionHasMore={sandboxExecutionPage.has_more} listLoading={sandboxListLoading} live={sandboxLive} loading={sandboxLoading} logLoading={sandboxLogLoading} error={sandboxError} onRefresh={() => void loadSandbox(false)} onLoadMoreRuntimes={() => void loadSandboxMore("runtimes")} onLoadMoreExecutions={() => void loadSandboxMore("executions")} onDrain={(runtimeId) => void drainSandbox(runtimeId)} />;
    if (page === "audit") return <AuthorizationAuditPage onAuthFailure={handleAuthFailure} />;
    if (!overview) return null;
    if (page === "traces") return <TraceExplorerPage days={days} initialFocus={traceFocus} initialQuery={traceQuery} />;
    if (page === "events") return <Suspense fallback={<div className="mon-fatal"><RefreshCw className="spin" /><strong>正在打开实时诊断…</strong></div>}><LiveDiagnosticsPage events={events} live={live} loading={eventsLoading} error={eventsError} onRefresh={() => void loadEvents()} onOpenTrace={(traceId) => void openTraceById(traceId)} /></Suspense>;
    if (page === "usage") return <MonitorUsagePage data={overview} usage={usage} systemUsage={systemUsage} />;
    if (page === "components") return <MonitorComponentsPage data={overview} systemUsage={systemUsage} onOpenTrace={navigateToTraceFilter} />;
    if (page === "errors") return <MonitorErrorsPage days={days} onOpenProblem={navigateToTraceFilter} />;
    if (page === "storage") return <MonitorStoragePage storage={storage} retentionDays={storageRetentionDays(storage)} onPrune={async () => { setStorage(await monitorApi.prune()); }} />;
    return <MonitorOverviewPage data={overview} usage={usage} systemUsage={systemUsage} />;
  }, [days, drainSandbox, events, eventsError, eventsLoading, handleAuthFailure, live, loadEvents, loadSandbox, loadSandboxMore, navigateToTraceFilter, openTraceById, overview, page, sandboxError, sandboxExecutionPage, sandboxExecutions, sandboxListLoading, sandboxLive, sandboxLoading, sandboxLogLoading, sandboxLogs, sandboxOverview, sandboxRuntimePage, sandboxRuntimes, storage, systemUsage, traceFocus, traceQuery, usage]);
  if (authState === "checking") return <main className="monitor-auth-shell"><div className="monitor-auth-loading"><RefreshCw className="spin" /><span>正在验证监控权限…</span></div></main>;
  if (authState === "login") return <MonitorLoginPage message={authMessageText} onLogin={login} />;
  const canReset = monitorSession?.permissions?.includes("system:runtime:reset") ?? false;
  return <div className="monitor-shell"><aside className="monitor-nav"><div className="monitor-brand"><Server /><span><strong>NLP Monitor</strong><small>OBSERVABILITY · 8766</small></span></div><nav className="monitor-nav-groups">{NAV_GROUPS.map((group) => <div className="monitor-nav-group" key={group.label}><span className="monitor-nav-group-label">{group.label}</span>{group.items.map(({ page: item, label, icon: Icon }) => <button className={page === item ? "active" : ""} aria-current={page === item ? "page" : undefined} type="button" key={item} onClick={() => navigate(item)}><Icon size={17} />{label}</button>)}</div>)}</nav><a href={controlPlaneUrl()}>返回控制面</a></aside><main><a className="monitor-skip-link" href="#monitor-content">跳到主要内容</a><header className="monitor-top"><div><h1>{NAV.find((item) => item.page === page)?.label}</h1><span><i className={`mon-live-dot ${(live || sandboxLive) ? "on" : ""}`} />{(live || sandboxLive) ? "实时" : "离线"}</span></div><label>统计周期<select value={days} onChange={(event) => setDays(Number(event.target.value))}><option value={1}>24 小时</option><option value={7}>7 天</option><option value={30}>30 天</option><option value={90}>90 天</option></select></label>{canReset ? <div className="mon-danger-menu"><button className="mon-danger-trigger" type="button" aria-label="更多监控操作" aria-expanded={dangerOpen} onClick={() => setDangerOpen((open) => !open)}><MoreHorizontal size={17} /></button>{dangerOpen ? <div className="mon-danger-menu-popover"><span>危险操作</span><button className="mon-reset-button" type="button" onClick={() => { setDangerOpen(false); setResetOpen(true); }} disabled={loading || resetting}><Trash2 />重置全部数据</button></div> : null}</div> : null}<button type="button" onClick={() => page === "sandbox" ? void loadSandbox(false) : page === "events" ? void Promise.all([load(), loadEvents()]) : void load()} disabled={loading || resetting || sandboxLoading || eventsLoading}><RefreshCw className={(loading || sandboxLoading || eventsLoading) ? "spin" : ""} />刷新</button></header><div className="monitor-content" id="monitor-content">{error && page !== "sandbox" ? <div className="mon-fatal"><AlertTriangle /><strong>监控数据加载失败</strong><p>{error}</p></div> : loading && !overview && page !== "sandbox" ? <div className="mon-fatal"><RefreshCw className="spin" /><strong>正在连接 Monitor</strong></div> : pageContent}</div></main>{chain && <ChainDrawer chain={chain} onClose={() => setChain(null)} onOpenTrace={(trace) => void openTrace(trace)} />}{detail && <TraceDrawer detail={detail} safeMode={detailSafeMode} onClose={() => setDetail(null)} />}<ConfirmDialog open={resetOpen} title="重置全部本地运行数据？" description="将永久清除所有学生会话、消息、练习记录、学习记忆、Trace、日志、调试事件和工具审计。教师主题、知识点、蓝图、模型与用户设置会保留。请先停止正在运行的对话。" confirmLabel={resetting ? "正在重置…" : "确认重置全部数据"} cancelLabel="取消" onClose={() => { if (!resetting) setResetOpen(false); }} onConfirm={() => void resetAll()} /></div>;
}
export function Json({ value }: { value: unknown }) { return <pre className="mon-json">{JSON.stringify(value, null, 2)}</pre>; }
