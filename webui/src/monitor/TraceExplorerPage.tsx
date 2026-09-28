import { AlertTriangle, ArrowLeft, CheckCircle2, ChevronLeft, ChevronRight, Clock3, GitBranch, Search, UserRound, X, XCircle, Zap } from "lucide-react";
import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { monitorApi, type Span, type Trace, type TraceGroupDetail, type TraceGroupSummary } from "./api";

const TRACE_PAGE_SIZE = 24;
type TraceFocus = "all" | "errors" | "slow";

function fmt(value: number | null | undefined, suffix = "") { return value == null ? "—" : `${value.toLocaleString()}${suffix}`; }
function moment(value?: string) { return value ? new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value)) : "—"; }
function statusText(value: string) { return value === "error" || value === "timeout" ? "异常" : value === "ok" ? "正常" : value === "running" ? "进行中" : value; }
function statusClass(value: string) { return value === "error" || value === "timeout" || value === "cancelled" || value === "denied" ? "danger" : value === "ok" ? "success" : "neutral"; }
function spanAttribute(span: Span, key: string) { const value = span.attributes?.[key]; return typeof value === "string" && value ? value : undefined; }

function FocusButton({ value, active, label, count, onClick }: { value: TraceFocus; active: boolean; label: string; count?: number; onClick: (value: TraceFocus) => void }) {
  return <button className={`mon-trace-focus ${active ? "active" : ""}`} type="button" onClick={() => onClick(value)}>{label}{count == null ? null : <b>{count}</b>}</button>;
}

function TraceGroupRow({ row, selected, onOpen }: { row: TraceGroupSummary; selected: boolean; onOpen: (id: string) => void }) {
  const issue = row.error_count > 0;
  const slowMs = row.max_duration_ms ?? row.total_duration_ms;
  return <button className={`mon-trace-group-row ${selected ? "selected" : ""} ${issue ? "issue" : ""}`} type="button" aria-label={`${row.chain_name} ${row.entrypoint} ${row.error_kinds.join(" ")} ${row.error_count} 个错误`} onClick={() => onOpen(row.chain_id)}>
    <span className={`mon-trace-severity ${issue ? "danger" : row.total_duration_ms >= 1000 ? "warn" : "success"}`}>{issue ? <AlertTriangle size={15} /> : row.total_duration_ms >= 1000 ? <Clock3 size={15} /> : <CheckCircle2 size={15} />}</span>
    <span className="mon-trace-group-main"><span className="mon-trace-group-title"><strong>{row.chain_name}</strong><code>{row.entrypoint}</code></span><small><UserRound size={11} />{row.user_ids.join("、") || "未知用户"}<i>·</i><GitBranch size={11} />{row.chain_id}</small>{row.error_kinds.length ? <span className="mon-trace-error-tags">{row.error_kinds.slice(0, 3).map((kind) => <em key={kind}>{kind}</em>)}</span> : null}</span>
    <span className="mon-trace-group-stats"><b>{row.trace_count}</b><small>Trace</small><b className={issue ? "danger-text" : ""}>{row.error_count}</b><small>问题</small><b>{fmt(slowMs, " ms")}</b><small>最长耗时</small></span>
    <time>{moment(row.last_seen)}</time>
  </button>;
}

function traceStatus(trace: Trace) { return <span className={`mon-trace-status ${statusClass(trace.status)}`}>{statusText(trace.status)}</span>; }

function SpanTree({ spans }: { spans: Array<Span & { trace_id?: string }> }) {
  const children = useMemo(() => {
    const byParent = new Map<string, Array<Span & { trace_id?: string }>>();
    spans.forEach((span) => { const key = span.parent_span_id ?? "__root__"; byParent.set(key, [...(byParent.get(key) ?? []), span]); });
    return byParent;
  }, [spans]);
  const render = (span: Span & { trace_id?: string }, depth: number, seen: Set<string>): ReactNode => {
    if (seen.has(span.span_id)) return null;
    const nextSeen = new Set(seen).add(span.span_id);
    const childrenRows = children.get(span.span_id) ?? [];
    const provider = spanAttribute(span, "provider");
    const model = spanAttribute(span, "model") ?? spanAttribute(span, "provider_model");
    return <div className="mon-trace-span-node" key={span.span_id} style={{ "--trace-depth": depth } as CSSProperties}><div className="mon-trace-span-line"><span className={`mon-trace-span-icon ${statusClass(span.status)}`}>{span.status === "error" || span.status === "timeout" ? <XCircle size={13} /> : <Zap size={13} />}</span><span className="mon-trace-span-copy"><strong>{span.name}</strong><small>{span.kind}{provider ? ` · ${provider}` : ""}{model ? ` · ${model}` : ""}</small></span><span className={`mon-trace-status ${statusClass(span.status)}`}>{statusText(span.status)}</span><b>{fmt(span.duration_ms, " ms")}</b><b>{fmt(span.total_tokens)} T</b>{span.attempt > 1 ? <em>第 {span.attempt} 次</em> : null}</div>{span.error_kind ? <div className="mon-trace-span-error"><AlertTriangle size={12} />{span.error_kind}{span.error_message ? `：${span.error_message}` : ""}</div> : null}{childrenRows.map((child) => render(child, depth + 1, nextSeen))}</div>;
  };
  const roots = spans.filter((span) => !span.parent_span_id || !spans.some((candidate) => candidate.span_id === span.parent_span_id));
  return <div className="mon-trace-span-tree">{roots.map((span) => render(span, 0, new Set()))}{!spans.length ? <div className="mon-trace-inline-empty">当前链路没有 Span 细节</div> : null}</div>;
}

function ChainDetail({ detail, onClose }: { detail: TraceGroupDetail; onClose: () => void }) {
  const issueSpans = detail.spans.filter((span) => ["error", "timeout", "cancelled", "denied"].includes(span.status) || span.error_kind);
  const issueEvents = detail.events.filter((event) => event.level === "error" || event.level === "warning");
  return <section className="mon-panel mon-trace-detail-panel"><header className="mon-trace-detail-header"><div><span className="mon-panel-kicker">CHAIN INSPECTOR · ALL USERS</span><h2>链路详情</h2><p>{detail.chain.chain_name} · {detail.chain.entrypoint}</p></div><button type="button" aria-label="关闭链路详情" onClick={onClose}><X size={17} /></button></header><div className="mon-trace-detail-body"><section className="mon-trace-detail-identity"><div><span>链路名称</span><strong>{detail.chain.chain_name}</strong><small>{detail.chain.chain_id}</small></div><div><span>归属用户</span><strong>{detail.chain.user_ids.join("、") || "未知用户"}</strong><small>{detail.chain.workspace_ids.join("、") || "未知工作区"}</small></div><div><span>问题信号</span><strong className={detail.chain.error_count ? "danger-text" : "success-text"}>{detail.chain.error_count ? `${detail.chain.error_count} 个问题` : "未发现错误"}</strong><small>{detail.chain.error_kinds.join(" · ") || "状态正常"}</small></div><div><span>时间范围</span><strong>{fmt(detail.chain.total_duration_ms, " ms")}</strong><small>{moment(detail.chain.started_at)} — {moment(detail.chain.last_seen)}</small></div></section>{detail.chain.error_count ? <div className="mon-trace-problem-callout"><AlertTriangle size={16} /><span><strong>优先检查异常 Span</strong><small>{detail.chain.error_kinds.join("、") || "链路状态异常"}。下面已将失败节点置顶标色，可直接定位 Provider、模型或工具。</small></span></div> : null}<section className="mon-trace-detail-section"><header><div><h3>调用时间线</h3><p>{detail.traces.length} 条 Trace 按时间排列，展开后查看具体 Span。</p></div></header><div className="mon-trace-trace-list">{detail.traces.map((trace, index) => <article key={trace.trace_id}><span className="mon-trace-sequence">{index + 1}</span><div><div className="mon-trace-trace-title"><strong>{trace.source === "worker_resume" ? "后台恢复" : "入口请求"}</strong>{traceStatus(trace)}</div><small>{moment(trace.started_at)} · {trace.trace_id}</small></div><span><b>{fmt(trace.duration_ms, " ms")}</b><small>耗时</small></span><span><b>{fmt(trace.total_tokens)}</b><small>Token</small></span></article>)}</div></section><section className="mon-trace-detail-section"><header><div><h3>Span 调用树</h3><p>按父子关系展示 Coordinator、Worker、Model、Tool 的真实执行顺序。</p></div><span className="mon-trace-issue-count">{issueSpans.length ? `${issueSpans.length} 个异常节点` : "无异常节点"}</span></header><SpanTree spans={detail.spans} /></section>{issueEvents.length ? <section className="mon-trace-detail-section"><header><div><h3>问题事件</h3><p>只显示 warning / error，减少排障噪音。</p></div></header><div className="mon-trace-event-list">{issueEvents.map((event) => <article key={event.event_id}><time>{moment(event.timestamp)}</time><span className={`mon-trace-status ${event.level === "error" ? "danger" : "neutral"}`}>{event.level}</span><strong>{event.name}</strong><code>{event.trace_id ?? "未关联 Trace"}</code><pre>{JSON.stringify(event.payload, null, 2)}</pre></article>)}</div></section> : null}</div></section>;
}

export function TraceExplorerPage({ days, initialFocus = "all", initialQuery = "" }: { days: number; initialFocus?: TraceFocus; initialQuery?: string }) {
  const [focus, setFocus] = useState<TraceFocus>(initialFocus);
  const [query, setQuery] = useState(initialQuery);
  const [appliedQuery, setAppliedQuery] = useState(initialQuery);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Awaited<ReturnType<typeof monitorApi.traceGroups>> | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TraceGroupDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      setLoading(true);
      setError("");
      void monitorApi.traceGroups({ days, limit: TRACE_PAGE_SIZE, offset, focus, query: appliedQuery }).then((result) => {
        if (cancelled) return;
        setPage(result);
        setSelectedId((current) => current && result.items.some((item) => item.chain_id === current) ? current : null);
      }).catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)); }).finally(() => { if (!cancelled) setLoading(false); });
    });
    return () => { cancelled = true; };
  }, [appliedQuery, days, focus, offset]);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      if (!selectedId) { setDetail(null); return; }
      setDetailLoading(true);
      void monitorApi.traceGroup(selectedId).then((result) => { if (!cancelled) setDetail(result); }).catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)); }).finally(() => { if (!cancelled) setDetailLoading(false); });
    });
    return () => { cancelled = true; };
  }, [selectedId]);

  const submitQuery = () => { setOffset(0); setAppliedQuery(query.trim()); };
  const items = page?.items ?? [];
  return <div className="mon-page mon-traces-page"><section className="mon-page-intro"><div><span className="mon-section-kicker">TRACE EXPLORER · ALL USERS</span><h2>运行链路</h2><p>先看问题链路，再下钻到具体用户、入口、Provider、模型和 Span。列表按链路聚合，不再平铺所有 Trace。</p></div><div className="mon-trace-page-guide"><AlertTriangle size={15} /><span><strong>发现问题</strong><small>错误优先 · 慢链路其次 · 详情按需加载</small></span></div></section><section className="mon-trace-toolbar"><div className="mon-trace-focus-tabs"><FocusButton value="all" active={focus === "all"} label="全部链路" count={focus === "all" ? page?.total : undefined} onClick={(value) => { setFocus(value); setOffset(0); }} /><FocusButton value="errors" active={focus === "errors"} label="有错误" onClick={(value) => { setFocus(value); setOffset(0); }} /><FocusButton value="slow" active={focus === "slow"} label="慢链路" onClick={(value) => { setFocus(value); setOffset(0); }} /></div><label className="mon-search mon-trace-search"><Search size={14} /><input aria-label="搜索链路" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") submitQuery(); }} placeholder="搜索链路、入口、用户或工作区" /><button type="button" aria-label="执行搜索" onClick={submitQuery}><Search size={13} /></button></label></section><div className="mon-trace-explorer"><section className="mon-panel mon-trace-list-panel"><header><div><span className="mon-panel-kicker">PROBLEM-ORIENTED GROUPS</span><h3>{focus === "errors" ? "异常链路" : focus === "slow" ? "慢链路" : "链路概览"}</h3><p>{page ? `${page.total.toLocaleString()} 个链路组 · 第 ${Math.floor(offset / TRACE_PAGE_SIZE) + 1} 页` : "正在读取聚合数据…"}</p></div>{loading ? <span className="mon-trace-loading">读取中…</span> : null}</header><div className="mon-trace-group-list">{error ? <div className="mon-trace-inline-error"><AlertTriangle size={15} />{error}</div> : null}{items.map((row) => <TraceGroupRow key={row.chain_id} row={row} selected={row.chain_id === selectedId} onOpen={setSelectedId} />)}{!loading && !error && !items.length ? <div className="mon-trace-inline-empty">当前筛选条件没有链路</div> : null}</div><footer className="mon-trace-pagination"><span>{page?.total ? `${offset + 1}-${Math.min(offset + items.length, page.total)} / ${page.total}` : "0 个链路组"}</span><div><button type="button" aria-label="上一页链路" disabled={offset === 0 || loading} onClick={() => setOffset((current) => Math.max(0, current - TRACE_PAGE_SIZE))}><ChevronLeft size={14} />上一页</button><button type="button" aria-label="下一页链路" disabled={!page?.has_more || loading} onClick={() => setOffset((current) => current + TRACE_PAGE_SIZE)}>下一页<ChevronRight size={14} /></button></div></footer></section>{detailLoading ? <section className="mon-panel mon-trace-detail-placeholder"><ArrowLeft size={18} /><strong>正在加载链路详情…</strong><small>只读取当前选中的链路，不会拉取其他 Trace。</small></section> : detail ? <ChainDetail detail={detail} onClose={() => { setSelectedId(null); setDetail(null); }} /> : <section className="mon-panel mon-trace-detail-placeholder"><GitBranch size={24} /><strong>选择一条链路开始排障</strong><small>建议先从“有错误”或“慢链路”开始，详情会展示用户归属、入口和完整 Span 调用树。</small></section>}</div></div>;
}
