import { useCallback, useEffect, useState } from "react";

import { api } from "@/platform/http/api";
import type { AuthorizationAuditItem } from "@/shared/types";

export function AuditLogPage() {
  const [items, setItems] = useState<AuthorizationAuditItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await api.listAuthorizationAudit(200);
      setItems(resp.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    queueMicrotask(() => void load());
  }, [load]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">授权审计日志</h1>
        <p className="text-sm text-gray-500">记录每一次权限放行与拒绝</p>
      </div>
      {error && <div className="rounded bg-red-50 p-4 text-sm text-red-700">{error}</div>}
      {loading ? (
        <div className="py-12 text-center text-gray-500">加载中...</div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                {["时间", "决策", "操作人", "目标用户", "权限码", "资源", "原因"].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 bg-white">
              {items.map((it) => (
                <tr key={it.id} className="text-sm">
                  <td className="whitespace-nowrap px-4 py-2 text-gray-500">{new Date(it.created_at).toLocaleString("zh-CN")}</td>
                  <td className="px-4 py-2">
                    <span className={`rounded px-2 py-0.5 text-xs font-semibold ${it.decision === "allow" ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}>{it.decision}</span>
                  </td>
                  <td className="px-4 py-2 text-gray-700">{it.actor_user_id ?? "-"}</td>
                  <td className="px-4 py-2 text-gray-700">{it.target_user_id ?? "-"}</td>
                  <td className="px-4 py-2 text-gray-700">{it.permission_code ?? "-"}</td>
                  <td className="px-4 py-2 text-gray-700">{it.resource_type ? `${it.resource_type}:${it.resource_id ?? ""}` : "-"}</td>
                  <td className="px-4 py-2 text-gray-500">{it.reason_code ?? "-"}</td>
                </tr>
              ))}
              {items.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-8 text-center text-sm text-gray-500">暂无审计记录</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
