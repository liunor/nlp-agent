import { Activity, AlertTriangle, Bot, Clock3, Database, Gauge, HardDrive, Radio, RefreshCw, Search, Server, Timer, Trash2, X, Zap } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ConfirmDialog } from "@/shared/ui/ConfirmDialog";
import { authenticate, monitorApi, type ErrorRow, type Overview, type SessionRow, type TelemetryEvent, type Trace, type TraceDetail, type UsageRow } from "./api";

export function controlPlaneUrl(current: Pick<Location, "protocol" | "hostname"> = location): string {
  return `${current.protocol}//${current.hostname}:8765/developer`;
}

export function telemetryFrame(data: unknown): { type: string; payload: TelemetryEvent } | null {
  try {
    const frame = JSON.parse(String(data)) as { type?: unknown; payload?: unknown };
    const payload = frame.payload as Partial<TelemetryEvent> | undefined;
    return frame.type === "telemetry.event"
      && typeof payload === "object" && payload !== null && !Array.isArray(payload)
      && typeof payload.event_id === "string" && payload.event_id.length > 0
      && typeof payload.timestamp === "string" && Number.isFinite(Date.parse(payload.timestamp))
      && typeof payload.level === "string" && payload.level.length > 0
      && typeof payload.name === "string" && payload.name.length > 0
      && typeof payload.payload === "object" && payload.payload !== null && !Array.isArray(payload.payload)
      ? frame as { type: string; payload: TelemetryEvent }
      : null;
  } catch {
    return null;
  }
}

export async function resetMonitorData(reset: () => Promise<unknown>, reload: () => Promise<unknown>) {
  await reset();
  await reload();
}

export interface EventGroup {
  trace: Trace | undefined;
  traceId: string | undefined;
  events: TelemetryEvent[];
}

export interface TraceChain {
  key: string;
  sessionId: string;
  turnId: string;
  evaluationRunId?: string;
  evaluationSuiteId?: string;
  traces: Trace[];
}

/** A user turn may produce a primary trace plus worker-resume traces.  Evaluation
 * cases intentionally have isolated sessions, so their shared evaluation run is
 * the parent chain shown in the Monitor. */
export function groupTracesIntoChains(traces: Trace[]): TraceChain[] {
  const chains = new Map<string, TraceChain>();
  for (const trace of traces) {
    const evaluationRunId = typeof trace.attributes.evaluation_run_id === "string" ? trace.attributes.evaluation_run_id : undefined;
    const evaluationSuiteId = typeof trace.attributes.evaluation_suite_id === "string" ? trace.attributes.evaluation_suite_id : undefined;
    const key = evaluationRunId ? `evaluation\u0000${evaluationRunId}` : `${trace.session_id}\u0000${trace.turn_id || trace.trace_id}`;
    const chain = chains.get(key) ?? {
      key,
      sessionId: evaluationRunId ? "evaluation" : trace.session_id,
      turnId: evaluationRunId ?? trace.turn_id,
      evaluationRunId,
      evaluationSuiteId,
      traces: [],
    };
    chain.traces.push(trace);
    chains.set(key, chain);
  }
  return [...chains.values()]
    .map((chain) => ({ ...chain, traces: [...chain.traces].sort((left, right) => Date.parse(left.started_at) - Date.parse(right.started_at)) }))
    .sort((left, right) => Date.parse(right.traces.at(-1)?.started_at ?? "") - Date.parse(left.traces.at(-1)?.started_at ?? ""));
}

/** Keeps events in the same run they originated from instead of one global feed. */
export function groupEventsByTrace(events: TelemetryEvent[], traces: Trace[]): EventGroup[] {
  const traceById = new Map(traces.map((trace) => [trace.trace_id, trace]));
  const groups = new Map<string, EventGroup>();
  for (const event of events) {
    const key = event.trace_id ?? "__unlinked__";
    const group = groups.get(key) ?? {
      trace: event.trace_id ? traceById.get(event.trace_id) : undefined,
      traceId: event.trace_id,
      events: [],
    };
    group.events.push(event);
    groups.set(key, group);
  }
  return [...groups.values()].sort((left, right) =>
    Date.parse(right.events[0]?.timestamp ?? "") - Date.parse(left.events[0]?.timestamp ?? ""),
  );
}

type Page = "overview" | "traces" | "sessions" | "errors" | "events" | "storage";
const NAV: Array<{ page: Page; label: string; icon: typeof Gauge }> = [
  { page: "overview", label: "系统总览", icon: Gauge },
  { page: "traces", label: "运行记录", icon: Activity },
  { page: "sessions", label: "Sessions", icon: Bot },
  { page: "errors", label: "错误分析", icon: AlertTriangle },
  { page: "events", label: "实时运行流", icon: Radio },
  { page: "storage", label: "数据留存", icon: HardDrive },
];
export function monitorPageFromLocation(current: Pick<Location, "search"> = location): Page {
  const candidate = new URLSearchParams(current.search).get("page");
  return NAV.some((item) => item.page === candidate) ? candidate as Page : "overview";
}

function fmt(value: number | null | undefined, suffix = "") { return value == null ? "—" : `${value.toLocaleString()}${suffix}`; }
function time(value?: string) { return value ? new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value)) : "—"; }
function Status({ value }: { value: string }) { return <span className={`mon-status ${value}`}>{value}</span>; }
function Empty({ text }: { text: string }) { return <div className="mon-empty"><Database /><span>{text}</span></div>; }

function OverviewPage({ data, usage }: { data: Overview; usage: UsageRow[] }) {
  const max = Math.max(1, ...usage.map((row) => row.total_tokens));
  return <div className="mon-stack"><div className="mon-kpis"><article><Zap /><span>请求</span><strong>{fmt(data.requests)}</strong><small>{data.period_days} 天</small></article><article><Timer /><span>P95 响应</span><strong>{fmt(data.latency_ms.p95, " ms")}</strong><small>P50 {fmt(data.latency_ms.p50, " ms")}</small></article><article><Clock3 /><span>P95 首 Token</span><strong>{fmt(data.ttft_ms.p95, " ms")}</strong><small>P50 {fmt(data.ttft_ms.p50, " ms")}</small></article><article className={data.error_rate > .05 ? "danger" : ""}><AlertTriangle /><span>错误率</span><strong>{(data.error_rate * 100).toFixed(2)}%</strong><small>{data.errors} errors</small></article></div><section className="mon-panel"><header><div><h2>Token 与缓存</h2><p>Provider 返回的输入、输出、推理和 KV Cache 命中数据</p></div></header><div className="mon-token-grid">{Object.entries(data.tokens).map(([key, value]) => <article key={key}><span>{key.replaceAll("_", " ")}</span><strong>{fmt(value)}</strong></article>)}</div></section><section className="mon-panel"><header><div><h2>用量趋势</h2><p>按组件与模型聚合的本地 Token 记录</p></div></header>{usage.length ? <div className="mon-bars">{usage.slice(-24).map((row, index) => <div key={`${row.day}-${row.component}-${row.name}-${index}`} title={`${row.day} ${row.name}: ${row.total_tokens}`}><span style={{ height: `${Math.max(3, row.total_tokens / max * 100)}%` }} /><small>{row.component.slice(0, 3)}</small></div>)}</div> : <Empty text="还没有 Token 用量数据" />}</section></div>;
}

function RunList({ chains, onOpen }: { chains: TraceChain[]; onOpen: (chain: TraceChain) => void }) {
  const [query, setQuery] = useState("");
  const visible = chains.filter((chain) => `${chain.sessionId} ${chain.turnId} ${chain.traces.map((trace) => `${trace.trace_id} ${trace.status} ${trace.source}`).join(" ")}`.toLowerCase().includes(query.toLowerCase()));
  return <section className="mon-panel"><header><div><h2>运行链路</h2><p>一次评测运行作为一个抽屉；普通对话按 Turn 收纳，内部再展示对应的 Trace。</p></div><label className="mon-search"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="评测 / Case / Trace / Session" /></label></header><div className="mon-run-list">{visible.map((chain) => { const latest = chain.traces.at(-1)!; const totalTokens = chain.traces.reduce((sum, trace) => sum + trace.total_tokens, 0); const cases = new Set(chain.traces.map((trace) => trace.attributes.evaluation_case_id).filter((value): value is string => typeof value === "string")); return <button className={`mon-run-card mon-chain-card ${chain.evaluationRunId ? "mon-evaluation-card" : ""}`} type="button" key={chain.key} onClick={() => onOpen(chain)}><time>{time(chain.traces[0]?.started_at)}</time><div className="mon-run-title">{chain.evaluationRunId ? <><span>评测批次</span><strong>运行 <code>{chain.evaluationRunId.slice(0, 12)}</code></strong><small>{chain.evaluationSuiteId ?? "evaluation"} · {cases.size} 个 Case · {chain.traces.length} 条 Trace</small></> : <><strong>对话链路 <code>{chain.turnId.slice(0, 12)}</code></strong><small>session {chain.sessionId.slice(0, 12)} · {chain.traces.length} 条 Trace</small></>}</div><Status value={latest.status} /><dl><div><dt>{chain.evaluationRunId ? "Case" : "Trace"}</dt><dd>{chain.evaluationRunId ? cases.size : chain.traces.length}</dd></div><div><dt>Trace</dt><dd>{chain.traces.length} 条</dd></div><div><dt>累计 Token</dt><dd>{fmt(totalTokens)}</dd></div><div><dt>{chain.evaluationRunId ? "Suite" : "会话"}</dt><dd><code>{(chain.evaluationSuiteId ?? chain.sessionId).slice(0, 12)}</code></dd></div></dl></button>; })}{!visible.length && <Empty text="没有匹配的运行链路" />}</div></section>;
}

function RunEventStream({ events, traces, live, onOpen }: { events: TelemetryEvent[]; traces: Trace[]; live: boolean; onOpen: (trace: Trace) => void }) {
  const groups = groupEventsByTrace(events, traces);
  return <section className="mon-panel"><header><div><h2>实时运行流</h2><p><i className={`mon-live-dot ${live ? "on" : ""}`} />{live ? "WebSocket 已连接；事件按一次运行归组。" : "实时连接已断开"}</p></div></header><div className="mon-run-event-stream">{groups.map((group) => <section className="mon-event-group" key={group.traceId ?? "unlinked"}><header><div><span>{group.trace ? "运行事件" : "未关联系统事件"}</span><strong>{group.trace ? `运行 ${group.trace.trace_id.slice(0, 12)}` : "没有 trace_id"}</strong><small>{group.trace ? `turn ${group.trace.turn_id.slice(0, 12)} · ${time(group.trace.started_at)}` : "这些事件不属于任何用户运行"}</small></div>{group.trace ? <button type="button" onClick={() => onOpen(group.trace!)}>查看完整 Trace</button> : null}<b>{group.events.length} 条</b></header><div className="mon-event-list">{group.events.map((event) => <article key={event.event_id}><time>{time(event.timestamp)}</time><Status value={event.level} /><strong>{event.name}</strong>{event.worker_id ? <code>worker {event.worker_id.slice(0, 10)}</code> : null}<pre>{JSON.stringify(event.payload)}</pre></article>)}</div></section>)}{!groups.length && <Empty text="还没有运行事件" />}</div></section>;
}

function TraceDrawer({ detail, onClose }: { detail: TraceDetail; onClose: () => void }) { const start = new Date(detail.trace.started_at).getTime(); const total = Math.max(detail.trace.duration_ms ?? 1, 1); return <><button className="mon-drawer-backdrop" type="button" onClick={onClose} aria-label="关闭" /><aside className="mon-drawer"><header><div><span>TRACE DETAIL</span><h2>{detail.trace.trace_id}</h2></div><button type="button" onClick={onClose}><X /></button></header><div className="mon-drawer-body"><div className="mon-detail-grid"><article><span>状态</span><Status value={detail.trace.status} /></article><article><span>总耗时</span><strong>{fmt(detail.trace.duration_ms, " ms")}</strong></article><article><span>首 Token</span><strong>{fmt(detail.trace.ttft_ms, " ms")}</strong></article><article><span>总 Token</span><strong>{fmt(detail.trace.total_tokens)}</strong></article></div><section><h3>Coordinator / Worker / Tool 时间线</h3><div className="mon-timeline">{detail.spans.map((span) => { const offset = Math.max(0, new Date(span.started_at).getTime() - start); return <article key={span.span_id}><div><strong>{span.name}</strong><span>{span.kind} · {span.worker_id ?? "coordinator"}</span></div><Status value={span.status} /><div className="mon-track"><i style={{ marginLeft: `${offset / total * 100}%`, width: `${Math.max(1, (span.duration_ms ?? 1) / total * 100)}%` }} /></div><small>{fmt(span.duration_ms, " ms")} · attempt {span.attempt} · {fmt(span.total_tokens)} tokens</small></article>; })}</div></section><section><h3>事件</h3><div className="mon-event-list">{detail.events.map((event) => <article key={event.event_id}><time>{time(event.timestamp)}</time><Status value={event.level} /><strong>{event.name}</strong><pre>{JSON.stringify(event.payload, null, 2)}</pre></article>)}</div></section><details><summary>原始 Trace / Tool JSON</summary><pre className="mon-json">{JSON.stringify(detail, null, 2)}</pre></details></div></aside></>; }

function ChainDrawer({ chain, onClose, onOpenTrace }: { chain: TraceChain; onClose: () => void; onOpenTrace: (trace: Trace) => void }) {
  return <><button className="mon-drawer-backdrop" type="button" onClick={onClose} aria-label="关闭运行链路" /><aside className="mon-drawer mon-chain-drawer"><header><div><span>{chain.evaluationRunId ? "EVALUATION RUN" : "RUN CHAIN"}</span><h2>{chain.evaluationRunId ? `运行 ${chain.evaluationRunId}` : `turn ${chain.turnId}`}</h2><small>{chain.evaluationRunId ? `${chain.evaluationSuiteId ?? "evaluation"} · 共 ${new Set(chain.traces.map((trace) => trace.attributes.evaluation_case_id)).size} 个 Case / ${chain.traces.length} 条 Trace` : `session ${chain.sessionId} · 共 ${chain.traces.length} 条 Trace`}</small></div><button type="button" onClick={onClose} aria-label="关闭"><X /></button></header><div className="mon-drawer-body"><section className="mon-chain-intro"><h3>{chain.evaluationRunId ? "本次评测的 Trace" : "本次链路的 Trace"}</h3><p>{chain.evaluationRunId ? "每个 Case 使用独立 Session，但全部属于这一次评测运行。点击卡片可查看 Worker、工具和完整 Trace。" : "按产生顺序排列。先查看入口 Trace，再检查 Worker 续接或后台恢复产生的后续 Trace。"}</p></section><div className="mon-chain-traces">{chain.traces.map((trace, index) => <button type="button" className="mon-chain-trace-card" key={trace.trace_id} onClick={() => onOpenTrace(trace)}><span className="mon-chain-sequence">{index + 1}</span><div><strong>{typeof trace.attributes.evaluation_case_id === "string" ? trace.attributes.evaluation_case_id : trace.source}</strong><small>{time(trace.started_at)} · trace {trace.trace_id.slice(0, 12)}</small></div><Status value={trace.status} /><dl><div><dt>响应</dt><dd>{fmt(trace.duration_ms, " ms")}</dd></div><div><dt>Token</dt><dd>{fmt(trace.total_tokens)}</dd></div><div><dt>首 Token</dt><dd>{fmt(trace.ttft_ms, " ms")}</dd></div></dl></button>)}</div></div></aside></>;
}

export function MonitorApp() {
  const [page, setPage] = useState<Page>(() => monitorPageFromLocation()); const [days, setDays] = useState(30); const [loading, setLoading] = useState(true); const [error, setError] = useState(""); const [overview, setOverview] = useState<Overview | null>(null); const [traces, setTraces] = useState<Trace[]>([]); const [usage, setUsage] = useState<UsageRow[]>([]); const [sessions, setSessions] = useState<SessionRow[]>([]); const [errors, setErrors] = useState<ErrorRow[]>([]); const [events, setEvents] = useState<TelemetryEvent[]>([]); const [storage, setStorage] = useState<Record<string, unknown>>({}); const [chain, setChain] = useState<TraceChain | null>(null); const [detail, setDetail] = useState<TraceDetail | null>(null); const [live, setLive] = useState(false); const [resetOpen, setResetOpen] = useState(false); const [resetting, setResetting] = useState(false);
  const load = useCallback(async () => { setLoading(true); setError(""); try { await authenticate(); const [o, t, u, s, e, ev, st] = await Promise.all([monitorApi.overview(days), monitorApi.traces(), monitorApi.usage(days), monitorApi.sessions(days), monitorApi.errors(days), monitorApi.events(), monitorApi.storage()]); setOverview(o); setTraces(t.items); setUsage(u.items); setSessions(s.items); setErrors(e.items); setEvents(ev.items); setStorage(st); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); } finally { setLoading(false); } }, [days]);
  useEffect(() => { queueMicrotask(() => void load()); }, [load]);
  useEffect(() => { const onPopState = () => setPage(monitorPageFromLocation()); addEventListener("popstate", onPopState); return () => removeEventListener("popstate", onPopState); }, []);
  useEffect(() => { if (!overview) return; let socket: WebSocket | null = null; let cancelled = false; void monitorApi.createWsTicket().then(({ ticket }) => { if (cancelled) return; const protocol = location.protocol === "https:" ? "wss:" : "ws:"; socket = new WebSocket(`${protocol}//${location.host}/ws/observability?ticket=${encodeURIComponent(ticket)}`); socket.onopen = () => setLive(true); socket.onclose = () => setLive(false); socket.onmessage = (message) => { const frame = telemetryFrame(message.data); if (frame) setEvents((current) => [frame.payload, ...current.filter((item) => item.event_id !== frame.payload.event_id)].slice(0, 300)); }; }).catch(() => setLive(false)); return () => { cancelled = true; socket?.close(); }; }, [overview]);
  const openTrace = async (trace: Trace) => setDetail(await monitorApi.trace(trace.trace_id));
  const chains = useMemo(() => groupTracesIntoChains(traces), [traces]);
  const resetAll = useCallback(async () => { setResetting(true); setError(""); try { await resetMonitorData(monitorApi.reset, load); setDetail(null); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); } finally { setResetting(false); setResetOpen(false); } }, [load]);
  const navigate = (next: Page) => { const url = new URL(location.href); if (next === "overview") url.searchParams.delete("page"); else url.searchParams.set("page", next); history.pushState({}, "", `${url.pathname}${url.search}${url.hash}`); setPage(next); };
  const pageContent = useMemo(() => { if (!overview) return null; if (page === "traces") return <RunList chains={chains} onOpen={setChain} />; if (page === "sessions") return <section className="mon-panel"><header><div><h2>活跃 Session</h2><p>请求量、错误、平均响应与 Token</p></div></header><div className="mon-table"><table><thead><tr><th>Session</th><th>用户 / Workspace</th><th>Turn</th><th>错误</th><th>平均响应</th><th>Token</th><th>最后活跃</th></tr></thead><tbody>{sessions.map((row) => <tr key={row.session_id}><td><code>{row.session_id}</code></td><td>{row.user_id}<small>{row.workspace_id} · {row.channel}</small></td><td>{row.turns}</td><td>{row.errors}</td><td>{fmt(row.avg_duration_ms, " ms")}</td><td>{fmt(row.total_tokens)}</td><td>{time(row.last_seen)}</td></tr>)}</tbody></table>{!sessions.length && <Empty text="没有 Session 数据" />}</div></section>; if (page === "errors") return <section className="mon-panel"><header><div><h2>错误、超时与重试</h2><p>按组件和错误类型聚合，点击样例 Trace 进入完整链路</p></div></header><div className="mon-error-grid">{errors.map((row) => <button key={`${row.error_kind}-${row.kind}-${row.name}`} type="button" onClick={() => { const trace = traces.find((item) => item.trace_id === row.sample_trace_id); if (trace) void openTrace(trace); }}><AlertTriangle /><span><strong>{row.error_kind}</strong><small>{row.kind} · {row.name}</small></span><b>{row.count}</b><time>{time(row.last_seen)}</time></button>)}</div>{!errors.length && <Empty text="当前周期没有错误" />}</section>; if (page === "events") return <RunEventStream events={events} traces={traces} live={live} onOpen={(trace) => void openTrace(trace)} />; if (page === "storage") return <section className="mon-panel"><header><div><h2>数据留存与清理</h2><p>Telemetry SQLite 大小、队列、丢弃事件和表记录数</p></div></header><div className="mon-storage"><Json value={storage} /><button type="button" onClick={async () => { if (confirm(`清理 ${days} 天以前的 Trace 与 Event？`)) { setStorage(await monitorApi.prune(days, days)); } }}><HardDrive size={16} />按当前周期清理过期数据</button></div></section>; return <OverviewPage data={overview} usage={usage} />; }, [chains, days, errors, events, live, overview, page, sessions, storage, traces, usage]);
  return <div className="monitor-shell"><aside className="monitor-nav"><div className="monitor-brand"><Server /><span><strong>NLP Monitor</strong><small>OBSERVABILITY · 8766</small></span></div><nav>{NAV.map(({ page: item, label, icon: Icon }) => <button className={page === item ? "active" : ""} type="button" key={item} onClick={() => navigate(item)}><Icon size={17} />{label}</button>)}</nav><a href={controlPlaneUrl()}>返回控制面</a></aside><main><header className="monitor-top"><div><h1>{NAV.find((item) => item.page === page)?.label}</h1><span><i className={`mon-live-dot ${live ? "on" : ""}`} />{live ? "实时" : "离线"}</span></div><label>统计周期<select value={days} onChange={(event) => setDays(Number(event.target.value))}><option value={1}>24 小时</option><option value={7}>7 天</option><option value={30}>30 天</option><option value={90}>90 天</option></select></label><button className="mon-reset-button" type="button" onClick={() => setResetOpen(true)} disabled={loading || resetting}><Trash2 />重置全部数据</button><button type="button" onClick={() => void load()} disabled={loading || resetting}><RefreshCw className={loading ? "spin" : ""} />刷新</button></header><div className="monitor-content">{error ? <div className="mon-fatal"><AlertTriangle /><strong>监控数据加载失败</strong><p>{error}</p></div> : loading && !overview ? <div className="mon-fatal"><RefreshCw className="spin" /><strong>正在连接 Monitor</strong></div> : pageContent}</div></main>{chain && <ChainDrawer chain={chain} onClose={() => setChain(null)} onOpenTrace={(trace) => void openTrace(trace)} />}{detail && <TraceDrawer detail={detail} onClose={() => setDetail(null)} />}<ConfirmDialog open={resetOpen} title="重置全部本地运行数据？" description="将永久清除所有学生会话、消息、练习记录、学习记忆、Trace、日志、调试事件和工具审计。教师主题、知识点、蓝图、模型与用户设置会保留。请先停止正在运行的对话。" confirmLabel={resetting ? "正在重置…" : "确认重置全部数据"} cancelLabel="取消" onClose={() => { if (!resetting) setResetOpen(false); }} onConfirm={() => void resetAll()} /></div>;
}
function Json({ value }: { value: unknown }) { return <pre className="mon-json">{JSON.stringify(value, null, 2)}</pre>; }
