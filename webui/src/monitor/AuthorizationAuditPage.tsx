import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { CheckCircle2, ChevronLeft, ChevronRight, FileWarning, Search, ShieldAlert } from "lucide-react";

import { monitorApi } from "./api";
import type { AuthorizationAuditListResponse, AuthorizationAuditRecord, AuthorizationAuditSummary } from "@/shared/types";

const PAGE_SIZE = 50;

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString("zh-CN");
}

function decisionLabel(value: string) {
  return value === "allow" ? "允许" : value === "deny" ? "拒绝" : value;
}

export function AuthorizationAuditPage() {
  const [page, setPage] = useState<AuthorizationAuditListResponse | null>(null);
  const [summary, setSummary] = useState<AuthorizationAuditSummary | null>(null);
  const [actorUserId, setActorUserId] = useState("");
  const [decision, setDecision] = useState("");
  const [reasonCode, setReasonCode] = useState("");
  const [applied, setApplied] = useState({ actorUserId: "", decision: "", reasonCode: "" });
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [nextPage, nextSummary] = await Promise.all([
        monitorApi.authorizationAudit({ limit: PAGE_SIZE, offset, actorUserId: applied.actorUserId || undefined, decision: applied.decision || undefined, reasonCode: applied.reasonCode || undefined }),
        monitorApi.authorizationAuditStats(30),
      ]);
      setPage(nextPage);
      setSummary(nextSummary);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [applied, offset]);

  useEffect(() => { queueMicrotask(() => void load()); }, [load]);

  const applyFilters = (event: FormEvent) => {
    event.preventDefault();
    setOffset(0);
    setApplied({ actorUserId: actorUserId.trim(), decision, reasonCode: reasonCode.trim() });
  };

  return <section className="mon-panel mon-audit-panel">
    <header className="mon-audit-header"><div><span className="mon-section-kicker">GOVERNANCE / AUTHORIZATION</span><h2>审计日志</h2><p>记录权限判定与敏感操作，帮助定位“谁在什么时候为什么被允许或拒绝”。详情 JSON 默认折叠。</p></div><ShieldAlert size={28} /></header>
    <div className="mon-audit-summary">{[["近 30 天总量", summary?.total ?? "-"], ["允许", summary?.by_decision.allow ?? 0], ["拒绝", summary?.by_decision.deny ?? 0]].map(([label, value]) => <article key={String(label)}><span>{label}</span><strong>{value}</strong></article>)}</div>
    <form className="mon-audit-filters" onSubmit={applyFilters}><label>操作者<input value={actorUserId} onChange={(event) => setActorUserId(event.target.value)} placeholder="用户 ID" /></label><label>判定<select value={decision} onChange={(event) => setDecision(event.target.value)}><option value="">全部</option><option value="allow">允许</option><option value="deny">拒绝</option></select></label><label>原因<input value={reasonCode} onChange={(event) => setReasonCode(event.target.value)} placeholder="permission_denied" /></label><button type="submit"><Search size={15} />筛选</button></form>
    {error && <div className="mon-audit-error"><FileWarning size={17} /><span>审计日志加载失败：{error}</span></div>}
    <div className="mon-audit-table"><table><thead><tr><th>时间</th><th>判定</th><th>原因</th><th>权限</th><th>资源</th><th>操作者</th><th>详情</th></tr></thead><tbody>{page?.items.map((row) => <AuditRow row={row} key={row.id} />)}</tbody></table>{loading && <div className="mon-audit-empty">正在读取审计记录…</div>}{!loading && !page?.items.length && <div className="mon-audit-empty">当前筛选条件没有记录</div>}</div>
    <footer className="mon-audit-pagination"><span>{page?.total ? `${offset + 1}-${Math.min(offset + PAGE_SIZE, page.total)} / ${page.total}` : "0 条记录"}</span><div><button type="button" disabled={offset === 0 || loading} onClick={() => setOffset((current) => Math.max(0, current - PAGE_SIZE))}><ChevronLeft size={15} />上一页</button><button type="button" disabled={!page?.has_more || loading} onClick={() => setOffset((current) => current + PAGE_SIZE)}>下一页<ChevronRight size={15} /></button></div></footer>
  </section>;
}

function AuditRow({ row }: { row: AuthorizationAuditRecord }) {
  return <tr><td>{formatDate(row.created_at)}</td><td><span className={`mon-audit-decision ${row.decision}`} >{row.decision === "allow" ? <CheckCircle2 size={14} /> : <ShieldAlert size={14} />}{decisionLabel(row.decision)}</span></td><td><code>{row.reason_code}</code></td><td><code>{row.permission_code ?? "-"}</code></td><td>{row.resource_type ? `${row.resource_type} / ${row.resource_id ?? "-"}` : "-"}</td><td>{row.actor_user_id ?? "系统"}</td><td><details><summary>查看</summary><pre>{JSON.stringify(row.detail, null, 2)}</pre></details></td></tr>;
}
