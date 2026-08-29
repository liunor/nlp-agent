import { useCallback, useEffect, useState } from "react";

import { api } from "@/platform/http/api";
import type { AuthorizationAuditRecord, AuthorizationAuditSummary } from "@/shared/types";

const PAGE_SIZE = 50;

export function AuditLogPageV2() {
  const [items, setItems] = useState<AuthorizationAuditRecord[]>([]);
  const [summary, setSummary] = useState<AuthorizationAuditSummary | null>(null);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [page, stats] = await Promise.all([
        api.listAuthorizationAudit({ limit: PAGE_SIZE, offset }),
        api.getAuthorizationAuditStats(30),
      ]);
      setItems(page.items);
      setTotal(page.total ?? page.items.length);
      setHasMore(page.has_more ?? false);
      setSummary(stats);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [offset]);

  useEffect(() => {
    queueMicrotask(() => void load());
  }, [load]);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">审计日志</h1>
          <p className="text-sm text-gray-500">
            服务端记录账号、角色、菜单、会话和授权决策；高频成功读取默认不重复落库。
          </p>
        </div>
        <button type="button" className="rounded border px-3 py-2 text-sm" onClick={() => void load()}>
          刷新
        </button>
      </div>

      {summary && (
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <p className="text-xs text-gray-500">近 30 天记录</p>
            <p className="mt-1 text-2xl font-bold text-gray-900">{summary.total}</p>
          </div>
          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <p className="text-xs text-gray-500">允许 / 拒绝</p>
            <p className="mt-1 text-2xl font-bold text-gray-900">
              {summary.by_decision.allow ?? 0} / {summary.by_decision.deny ?? 0}
            </p>
          </div>
          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <p className="text-xs text-gray-500">最高频原因</p>
            <p className="mt-1 truncate text-sm font-semibold text-gray-900">
              {summary.top_reasons[0]?.reason_code ?? "暂无"}
            </p>
          </div>
        </div>
      )}

      {error && <div className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</div>}
      <div className="overflow-x-auto rounded border border-gray-200 bg-white">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr>
              {["时间", "结果", "原因", "权限", "资源", "操作者", "详情"].map((title) => (
                <th key={title} className="px-4 py-3 text-left text-xs font-semibold text-gray-500">
                  {title}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {loading ? (
              <tr><td colSpan={7} className="px-4 py-10 text-center text-gray-500">加载中...</td></tr>
            ) : items.map((item) => (
              <tr key={item.id}>
                <td className="whitespace-nowrap px-4 py-3 text-gray-500">
                  {new Date(item.created_at).toLocaleString("zh-CN")}
                </td>
                <td className={`px-4 py-3 font-medium ${item.decision === "allow" ? "text-green-700" : "text-red-700"}`}>
                  {item.decision}
                </td>
                <td className="px-4 py-3">{item.reason_code}</td>
                <td className="px-4 py-3 text-xs text-gray-500">{item.permission_code ?? "-"}</td>
                <td className="px-4 py-3 text-xs">{item.resource_type ?? "-"}/{item.resource_id ?? "-"}</td>
                <td className="px-4 py-3 text-xs text-gray-500">{item.actor_user_id ?? "system"}</td>
                <td className="max-w-sm px-4 py-3"><pre className="overflow-auto text-xs">{JSON.stringify(item.detail)}</pre></td>
              </tr>
            ))}
            {!loading && !items.length && (
              <tr><td colSpan={7} className="px-4 py-10 text-center text-gray-500">暂无审计记录</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-sm text-gray-500">
        <span>{total ? `${offset + 1}-${Math.min(offset + PAGE_SIZE, total)} / ${total}` : "0 条"}</span>
        <div className="flex gap-2">
          <button type="button" className="rounded border px-3 py-1 disabled:opacity-40" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
            上一页
          </button>
          <button type="button" className="rounded border px-3 py-1 disabled:opacity-40" disabled={!hasMore || loading} onClick={() => setOffset(offset + PAGE_SIZE)}>
            下一页
          </button>
        </div>
      </div>
    </div>
  );
}
