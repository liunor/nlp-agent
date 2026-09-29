import { AlertTriangle, ArrowUpRight, CheckCircle2, Clock3, Radio, RefreshCw, Search, TimerReset, WifiOff } from "lucide-react";
import { useMemo, useState } from "react";
import type { TelemetryEvent } from "./api";
import { safeEventContext } from "./monitor-helpers";

export { safeEventContext } from "./monitor-helpers";

export type DiagnosticFilter = "all" | "errors" | "warnings" | "slow";

type DiagnosticGroup = {
  key: string;
  traceId?: string;
  events: TelemetryEvent[];
};

const DIAGNOSTIC_NAME_PATTERN = /(timeout|retry|backpressure|dropped?|disconnect|throttl|rate[_-]?limit|slow|fail(?:ed|ure)?|error|recover|circuit|queue)/i;
const SLOW_EVENT_THRESHOLD_MS = 1500;

function levelOf(event: TelemetryEvent) {
  return event.level.toLowerCase();
}

function isErrorLevel(event: TelemetryEvent) {
  return ["error", "critical", "fatal"].includes(levelOf(event));
}

function isWarningLevel(event: TelemetryEvent) {
  return ["warning", "warn"].includes(levelOf(event));
}

function isSlowEvent(event: TelemetryEvent) {
  const duration = event.payload.duration_ms ?? event.payload.latency_ms;
  return /(slow|latency|timeout)/i.test(event.name) || (typeof duration === "number" && duration >= SLOW_EVENT_THRESHOLD_MS);
}

export function isDiagnosticEvent(event: TelemetryEvent) {
  return isErrorLevel(event) || isWarningLevel(event) || DIAGNOSTIC_NAME_PATTERN.test(event.name) || isSlowEvent(event);
}

function scalarValue(value: unknown) {
  if (typeof value === "string" && value.length > 0 && value.length <= 80) return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return null;
}

function matchesFilter(event: TelemetryEvent, filter: DiagnosticFilter) {
  if (filter === "errors") return isErrorLevel(event);
  if (filter === "warnings") return isWarningLevel(event);
  if (filter === "slow") return isSlowEvent(event);
  return true;
}

export function filterDiagnosticEvents(events: TelemetryEvent[], filter: DiagnosticFilter, query: string) {
  const normalizedQuery = query.trim().toLowerCase();
  return events.filter((event) => {
    if (!isDiagnosticEvent(event) || !matchesFilter(event, filter)) return false;
    if (!normalizedQuery) return true;
    const searchable = [event.name, event.level, event.trace_id, event.session_id, event.turn_id, event.worker_id, ...safeEventContext(event)]
      .filter(Boolean).join(" ").toLowerCase();
    return searchable.includes(normalizedQuery);
  });
}

export function groupDiagnosticEvents(events: TelemetryEvent[]): DiagnosticGroup[] {
  const groups = new Map<string, DiagnosticGroup>();
  for (const event of events) {
    const provider = scalarValue(event.payload.provider_model) ?? scalarValue(event.payload.model) ?? scalarValue(event.payload.provider) ?? "unknown";
    const key = event.trace_id ? `trace:${event.trace_id}` : `signal:${event.name}:${provider}`;
    const group = groups.get(key) ?? { key, traceId: event.trace_id, events: [] };
    group.events.push(event);
    groups.set(key, group);
  }
  return [...groups.values()].sort((left, right) => Date.parse(right.events[0]?.timestamp ?? "") - Date.parse(left.events[0]?.timestamp ?? ""));
}

function shortId(value: string) {
  return value.length > 16 ? value.slice(0, 16) : value;
}

function levelLabel(value: string) {
  return ({ critical: "严重", error: "错误", fatal: "致命", warning: "警告", warn: "警告" } as Record<string, string>)[value.toLowerCase()] ?? "信号";
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value));
}

function number(value: number) {
  return value.toLocaleString("zh-CN");
}

function SummarySignal({ label, value, hint, tone = "neutral" }: { label: string; value: string; hint: string; tone?: "neutral" | "danger" | "warning" | "success" }) {
  return <div className={`mon-diagnostic-signal ${tone}`}><span>{label}</span><strong>{value}</strong><small>{hint}</small></div>;
}

const INITIAL_GROUP_COUNT = 12;
const GROUP_BATCH_SIZE = 12;
const MAX_EVENTS_PER_GROUP = 10;
const MAX_RENDERED_GROUP_COUNT = 36;

export function LiveDiagnosticsPage({ events, live, loading = false, error = "", onRefresh, onOpenTrace }: {
  events: TelemetryEvent[];
  live: boolean;
  loading?: boolean;
  error?: string;
  onRefresh: () => void;
  onOpenTrace: (traceId: string) => void;
}) {
  const [filter, setFilter] = useState<DiagnosticFilter>("all");
  const [query, setQuery] = useState("");
  const [visibleGroupCount, setVisibleGroupCount] = useState(INITIAL_GROUP_COUNT);
  const diagnostics = useMemo(() => filterDiagnosticEvents(events, filter, query), [events, filter, query]);
  const groups = useMemo(() => groupDiagnosticEvents(diagnostics), [diagnostics]);
  const allDiagnostics = useMemo(() => filterDiagnosticEvents(events, "all", ""), [events]);
  const errorCount = allDiagnostics.filter(isErrorLevel).length;
  const warningCount = allDiagnostics.filter(isWarningLevel).length;
  const slowCount = allDiagnostics.filter(isSlowEvent).length;
  const traceCount = new Set(allDiagnostics.map((event) => event.trace_id).filter(Boolean)).size;

  return <div className="mon-page mon-live-diagnostics-page">
    <header className="mon-page-intro"><div><span className="mon-section-kicker">LIVE DIAGNOSTICS · ALL USERS</span><h2>实时诊断</h2><p>只保留值得立即处理的异常信号，按链路聚合并脱敏展示；常规成功事件留在运行链路和历史分析中。</p></div><span className="mon-page-intro-meta">按需连接 · 最近 100 条</span></header>
    <section className="mon-diagnostic-summary" aria-label="实时诊断摘要">
      <SummarySignal label="异常信号" value={number(allDiagnostics.length)} hint="警告 / 错误 / 慢请求" tone={allDiagnostics.length ? "warning" : "success"} />
      <SummarySignal label="错误信号" value={number(errorCount)} hint="需要优先处理" tone={errorCount ? "danger" : "success"} />
      <SummarySignal label="受影响链路" value={number(traceCount)} hint="已关联 Trace" />
      <SummarySignal label="慢请求" value={number(slowCount)} hint={`${number(warningCount)} 个警告信号`} tone={slowCount ? "warning" : "success"} />
    </section>
    <section className="mon-panel mon-live-diagnostics-panel">
      <header><div><span className="mon-panel-kicker">SIGNAL STREAM</span><h2>异常信号流</h2><p><i className={`mon-live-dot ${live ? "on" : ""}`} />{live ? "WebSocket 已连接" : "实时连接已断开"} · 新信号保留在顶部，不会强制滚动 · 原始请求内容已隐藏</p></div><div className="mon-live-diagnostics-actions"><span className={live ? "mon-live-connection on" : "mon-live-connection"}>{live ? <Radio size={13} /> : <WifiOff size={13} />}{live ? "实时" : "离线"}</span><button type="button" onClick={onRefresh} disabled={loading}><RefreshCw size={13} className={loading ? "spin" : ""} />刷新</button></div></header>
      <div className="mon-diagnostic-toolbar"><div className="mon-diagnostic-filters" aria-label="诊断信号筛选">{(["all", "errors", "warnings", "slow"] as const).map((item) => { const count = item === "all" ? allDiagnostics.length : item === "errors" ? errorCount : item === "warnings" ? warningCount : slowCount; const label = item === "all" ? "全部信号" : item === "errors" ? "错误" : item === "warnings" ? "警告" : "慢请求"; return <button type="button" key={item} className={filter === item ? "active" : ""} aria-pressed={filter === item} onClick={() => setFilter(item)}>{label}<b>{count}</b></button>; })}</div><label className="mon-search mon-diagnostic-search"><Search size={14} /><span className="sr-only">搜索诊断信号</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索事件、Provider、模型或 Trace" /></label></div>
      {error ? <div className="mon-inline-warning"><AlertTriangle size={14} />{error}</div> : null}
      <div className="mon-diagnostic-list" aria-live="polite">{groups.slice(0, Math.min(visibleGroupCount, MAX_RENDERED_GROUP_COUNT)).map((group) => { const first = group.events[0]; const extraCount = Math.max(0, group.events.length - MAX_EVENTS_PER_GROUP); return <article className="mon-diagnostic-group" key={group.key}><header><div><span>{group.traceId ? "链路异常" : "系统信号"}</span><strong>{group.traceId ? `Trace ${shortId(group.traceId)}` : first.name}</strong><small>{group.traceId ? `${first.turn_id ? `Turn ${shortId(first.turn_id)} · ` : ""}${first.session_id ? `Session ${shortId(first.session_id)}` : "未标注会话"}` : "未关联用户运行，仅保留系统级信号"}</small></div>{group.traceId ? <button type="button" aria-label={`查看运行链路 ${group.traceId}`} onClick={() => onOpenTrace(group.traceId!)}>查看运行链路 <ArrowUpRight size={13} /></button> : null}<b>{group.events.length} 条</b></header><div className="mon-diagnostic-event-list">{group.events.slice(0, MAX_EVENTS_PER_GROUP).map((item) => <div className="mon-diagnostic-event" key={item.event_id}><span className={`mon-diagnostic-level ${isErrorLevel(item) ? "error" : "warning"}`}>{isErrorLevel(item) ? "错误" : isSlowEvent(item) ? "慢请求" : levelLabel(item.level)}</span><div><strong>{item.name}</strong><small>{formatTime(item.timestamp)}{item.span_id ? ` · Span ${shortId(item.span_id)}` : ""}</small><p>{safeEventContext(item).join(" · ") || "已隐藏原始 payload，仅保留诊断上下文"}</p></div></div>)}{extraCount ? <div className="mon-diagnostic-more">还有 {extraCount} 条同类信号，已折叠以保护页面性能</div> : null}</div></article>; })}{loading && !events.length ? <div className="mon-loading-row"><RefreshCw className="spin" size={14} />正在加载实时诊断…</div> : null}{!loading && !groups.length ? <div className="mon-diagnostic-empty"><CheckCircle2 size={19} /><strong>{query ? "没有匹配的异常信号" : "当前没有需要处理的异常信号"}</strong><span>{query ? "尝试清空搜索或切换筛选。" : "正常事件不会在这里刷屏，打开运行链路可查看完整过程。"}</span></div> : null}</div>
      {visibleGroupCount < Math.min(groups.length, MAX_RENDERED_GROUP_COUNT) ? <button className="mon-diagnostic-load-more" type="button" onClick={() => setVisibleGroupCount((current) => Math.min(current + GROUP_BATCH_SIZE, MAX_RENDERED_GROUP_COUNT))}>加载更多信号 <small>已显示 {Math.min(visibleGroupCount, groups.length)} / {Math.min(groups.length, MAX_RENDERED_GROUP_COUNT)} 组</small></button> : groups.length > MAX_RENDERED_GROUP_COUNT ? <div className="mon-diagnostic-more">为保护页面性能，最多展示最近 {MAX_RENDERED_GROUP_COUNT} 组；请使用搜索或筛选缩小范围</div> : null}
      <footer className="mon-diagnostic-footer"><span><Clock3 size={13} />最近 {number(events.length)} 条缓存事件中筛出 {number(diagnostics.length)} 条</span><span><TimerReset size={13} />刷新周期：5 分钟；离开此页面自动断开实时连接</span></footer>
    </section>
  </div>;
}
