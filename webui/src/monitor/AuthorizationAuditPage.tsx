import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { CheckCircle2, ChevronLeft, ChevronRight, FileWarning, Search, ShieldAlert } from "lucide-react";

import { monitorApi } from "./api";
import type { AuthorizationAuditListResponse, AuthorizationAuditRecord, AuthorizationAuditSummary } from "@/shared/types";

export const AUTHORIZATION_AUDIT_PAGE_SIZE = 20;

export interface AuthorizationAuditGroup {
  key: string;
  actorUserId: string | null;
  targetUserId: string | null;
  decision: string;
  reasonCode: string;
  permissionCode: string | null;
  resourceType: string | null;
  resourceIds: string[];
  latest: AuthorizationAuditRecord;
  items: AuthorizationAuditRecord[];
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString("zh-CN");
}

function decisionLabel(value: string) {
  return value === "allow" ? "允许" : value === "deny" ? "拒绝" : value;
}

function auditStatus(reason: unknown): number | undefined {
  if (typeof reason !== "object" || reason === null || !("status" in reason)) return undefined;
  const status = (reason as { status?: unknown }).status;
  return typeof status === "number" ? status : undefined;
}

export function groupAuthorizationAudit(rows: AuthorizationAuditRecord[]): AuthorizationAuditGroup[] {
  const groups = new Map<string, AuthorizationAuditGroup>();
  for (const row of rows) {
    const key = [
      row.actor_user_id ?? "system",
      row.target_user_id ?? "",
      row.decision,
      row.reason_code,
      row.permission_code ?? "",
      row.resource_type ?? "",
    ].join("|");
    const current = groups.get(key);
    if (current) {
      current.items.push(row);
      if (row.resource_id && !current.resourceIds.includes(row.resource_id)) current.resourceIds.push(row.resource_id);
      continue;
    }
    groups.set(key, {
      key,
      actorUserId: row.actor_user_id,
      targetUserId: row.target_user_id,
      decision: row.decision,
      reasonCode: row.reason_code,
      permissionCode: row.permission_code,
      resourceType: row.resource_type,
      resourceIds: row.resource_id ? [row.resource_id] : [],
      latest: row,
      items: [row],
    });
  }
  return [...groups.values()];
}

function AuditGroupRow({ group }: { group: AuthorizationAuditGroup }) {
  const [expanded, setExpanded] = useState(false);
  const resources = group.resourceIds.slice(0, 3).join("、") || "未指定资源";
  return <article className={`mon-audit-group ${group.decision}`}>
    <button type="button" className="mon-audit-group-summary" aria-expanded={expanded} aria-label={`${expanded ? "收起" : "展开"} ${group.reasonCode}`} onClick={() => setExpanded((current) => !current)}>
      <span className={`mon-audit-decision ${group.decision}`}>{group.decision === "allow" ? <CheckCircle2 size={14} /> : <ShieldAlert size={14} />}{decisionLabel(group.decision)}</span>
      <span className="mon-audit-group-main"><strong>{group.reasonCode}</strong><small>{group.permissionCode ?? "未标注权限"} · {group.resourceType ?? "系统资源"}</small></span>
      <span className="mon-audit-group-actor"><small>操作者</small><b>{group.actorUserId ?? "系统"}</b>{group.targetUserId ? <em>目标 {group.targetUserId}</em> : null}</span>
      <span className="mon-audit-group-count"><b>{group.items.length} 次</b><small>{group.items.length > 1 ? "已合并重复判定" : "单次判定"}</small></span>
      <time>{formatDate(group.latest.created_at)}</time>
      <span className="mon-audit-group-toggle">{expanded ? "收起详情" : "查看详情"}</span>
    </button>
    <div className="mon-audit-group-resource"><span>资源范围</span><code>{resources}{group.resourceIds.length > 3 ? ` 等 ${group.resourceIds.length} 个` : ""}</code></div>
    {expanded ? <div className="mon-audit-group-details">{group.items.slice(0, 5).map((row) => <div className="mon-audit-detail-row" key={row.id}><span>{formatDate(row.created_at)}</span><code>{row.id.slice(0, 12)}</code><pre>{JSON.stringify(row.detail, null, 2)}</pre></div>)}{group.items.length > 5 ? <small>其余 {group.items.length - 5} 条已折叠，使用分页继续查看。</small> : null}</div> : null}
  </article>;
}

export function AuthorizationAuditPage({ onAuthFailure }: { onAuthFailure?: (reason: unknown) => void } = {}) {
  const [page, setPage] = useState<AuthorizationAuditListResponse | null>(null);
  const [summary, setSummary] = useState<AuthorizationAuditSummary | null>(null);
  const [actorUserId, setActorUserId] = useState("");
  const [decision, setDecision] = useState("");
  const [reasonCode, setReasonCode] = useState("");
  const [applied, setApplied] = useState({ actorUserId: "", decision: "", reasonCode: "" });
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const requestSerial = useRef(0);
  const summaryRef = useRef<AuthorizationAuditSummary | null>(null);

  const load = useCallback(async () => {
    const serial = ++requestSerial.current;
    setLoading(true);
    setError("");
    try {
      const params: Parameters<typeof monitorApi.authorizationAudit>[0] = { limit: AUTHORIZATION_AUDIT_PAGE_SIZE, offset };
      if (applied.actorUserId) params.actorUserId = applied.actorUserId;
      if (applied.decision) params.decision = applied.decision;
      if (applied.reasonCode) params.reasonCode = applied.reasonCode;
      const [nextPage, nextSummary] = await Promise.all([
        monitorApi.authorizationAudit(params),
        offset === 0 ? monitorApi.authorizationAuditStats(30) : Promise.resolve(summaryRef.current),
      ]);
      if (serial !== requestSerial.current) return;
      setPage(nextPage);
      if (nextSummary) {
        summaryRef.current = nextSummary;
        setSummary(nextSummary);
      }
    } catch (reason) {
      if (serial !== requestSerial.current) return;
      if (auditStatus(reason) === 401 || auditStatus(reason) === 403) {
        onAuthFailure?.(reason);
        setError("登录状态已失效，请重新登录监控平台。");
      } else {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (serial === requestSerial.current) setLoading(false);
    }
  }, [applied, offset, onAuthFailure]);

  useEffect(() => { queueMicrotask(() => void load()); }, [load]);

  const groups = useMemo(() => groupAuthorizationAudit(page?.items ?? []), [page?.items]);
  const applyFilters = (event: FormEvent) => {
    event.preventDefault();
    setOffset(0);
    setApplied({ actorUserId: actorUserId.trim(), decision, reasonCode: reasonCode.trim() });
  };
  const start = page?.total ? offset + 1 : 0;
  const end = page?.total ? offset + (page.items.length || 0) : 0;

  return <div className="mon-page mon-audit-page">
    <header className="mon-page-intro"><div><span className="mon-section-kicker">GOVERNANCE / AUTHORIZATION</span><h2>审计日志</h2><p>只保留权限拒绝和敏感操作等治理信号；重复的授权判定在当前页合并，展开后再读取详情。</p></div><span className="mon-page-intro-meta">追加式 · 不含会话内容</span></header>
    <section className="mon-audit-summary"><article><span>近 30 天判定</span><strong>{summary?.total ?? "—"}</strong><small>审计原始记录</small></article><article><span>允许</span><strong className="success-text">{summary?.by_decision.allow ?? 0}</strong><small>已通过的权限判定</small></article><article><span>拒绝</span><strong className="danger-text">{summary?.by_decision.deny ?? 0}</strong><small>优先排查</small></article><article><span>当前页压缩</span><strong>{groups.length}</strong><small>{page?.items.length ?? 0} 条 → {groups.length} 组</small></article></section>
    <section className="mon-panel mon-audit-workspace">
      <form className="mon-audit-filters" onSubmit={applyFilters}><label>操作者<input value={actorUserId} onChange={(event) => setActorUserId(event.target.value)} placeholder="用户 ID" /></label><label>判定<select value={decision} onChange={(event) => setDecision(event.target.value)}><option value="">全部判定</option><option value="allow">允许</option><option value="deny">拒绝</option></select></label><label>原因<input value={reasonCode} onChange={(event) => setReasonCode(event.target.value)} placeholder="permission_denied" /></label><button type="submit"><Search size={15} />筛选</button></form>
      {summary?.top_reasons.length ? <div className="mon-audit-reason-strip"><span>高频原因</span>{summary.top_reasons.slice(0, 5).map((item) => <button type="button" key={item.reason_code} onClick={() => { setReasonCode(item.reason_code); setOffset(0); setApplied((current) => ({ ...current, reasonCode: item.reason_code })); }}><code>{item.reason_code}</code><b>{item.count}</b></button>)}</div> : null}
      {error && <div className="mon-audit-error"><FileWarning size={17} /><span>审计日志加载失败：{error}</span></div>}
      <div className="mon-audit-group-list" aria-busy={loading}>{groups.map((group) => <AuditGroupRow group={group} key={group.key} />)}{loading && !page ? <div className="mon-audit-empty">正在读取审计记录…</div> : null}{!loading && !groups.length && !error ? <div className="mon-audit-empty">当前筛选条件没有记录</div> : null}</div>
      <footer className="mon-audit-pagination"><span>{page?.total ? `${start}-${end} / ${page.total} 条审计记录 · 本页 ${groups.length} 组` : "0 条审计记录"}</span><div><button type="button" aria-label="上一页审计" disabled={offset === 0 || loading} onClick={() => setOffset((current) => Math.max(0, current - AUTHORIZATION_AUDIT_PAGE_SIZE))}><ChevronLeft size={15} />上一页</button><button type="button" aria-label="下一页审计" disabled={!page?.has_more || loading} onClick={() => setOffset((current) => current + AUTHORIZATION_AUDIT_PAGE_SIZE)}>下一页<ChevronRight size={15} /></button></div></footer>
    </section>
  </div>;
}
