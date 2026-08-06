import { useCallback, useEffect, useState } from "react";
import { api } from "@/platform/http/api";
import type { AgentSession, AgentSessionListResponse } from "@/shared/types";

export function AgentSessionListPage() {
  const [data, setData] = useState<AgentSessionListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const limit = 20;

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await api.listAgentSessions(statusFilter || undefined, limit, offset);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [offset, statusFilter]);

  useEffect(() => { queueMicrotask(() => void load()); }, [load]);

  const handleDelete = async (session: AgentSession) => {
    if (!confirm(`确认删除会话「${session.title}」？`)) return;
    try {
      await api.deleteAgentSession(session.id);
      await load();
    } catch (err) {
      alert(err instanceof Error ? err.message : "删除失败");
    }
  };

  const handleStatusChange = async (session: AgentSession, newStatus: string) => {
    try {
      await api.updateAgentSession(session.id, { status: newStatus });
      await load();
    } catch (err) {
      alert(err instanceof Error ? err.message : "更新失败");
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Agent 会话管理</h1>
          <p className="text-sm text-gray-500">查看和管理 Agent 会话</p>
        </div>
      </div>

      {/* Filter */}
      <div className="flex gap-3">
        <select
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); setOffset(0); }}
          className="rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          <option value="">全部状态</option>
          <option value="active">活跃</option>
          <option value="idle">空闲</option>
          <option value="archived">已归档</option>
        </select>
      </div>

      {error && <div className="rounded bg-red-50 p-4 text-sm text-red-700">{error}</div>}

      {loading && !data ? (
        <div className="py-12 text-center text-gray-500">加载中...</div>
      ) : (
        <>
          <div className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">标题</th>
                  <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">工作区</th>
                  <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">创建者</th>
                  <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">状态</th>
                  <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">创建时间</th>
                  <th className="px-6 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 bg-white">
                {data?.sessions.map((session) => (
                  <tr key={session.id} className="hover:bg-gray-50">
                    <td className="whitespace-nowrap px-6 py-4 text-sm font-medium text-gray-900">{session.title}</td>
                    <td className="whitespace-nowrap px-6 py-4 text-sm text-gray-500">{session.workspace_id}</td>
                    <td className="whitespace-nowrap px-6 py-4 text-sm text-gray-500">{session.created_by_user_id}</td>
                    <td className="whitespace-nowrap px-6 py-4">
                      <select
                        value={session.status}
                        onChange={(e) => void handleStatusChange(session, e.target.value)}
                        className={`rounded-full px-2 py-0.5 text-xs font-semibold border-0 ${
                          session.status === "active" ? "bg-green-100 text-green-800" :
                          session.status === "archived" ? "bg-gray-100 text-gray-600" :
                          "bg-yellow-100 text-yellow-800"
                        }`}
                      >
                        <option value="active">活跃</option>
                        <option value="idle">空闲</option>
                        <option value="archived">已归档</option>
                      </select>
                    </td>
                    <td className="whitespace-nowrap px-6 py-4 text-sm text-gray-500">
                      {new Date(session.created_at).toLocaleDateString("zh-CN")}
                    </td>
                    <td className="whitespace-nowrap px-6 py-4 text-right text-sm">
                      <button
                        type="button"
                        onClick={() => void handleDelete(session)}
                        className="rounded px-3 py-1 text-xs font-medium text-red-700 hover:bg-red-50"
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
                {(!data || data.sessions.length === 0) && (
                  <tr>
                    <td colSpan={6} className="px-6 py-8 text-center text-sm text-gray-500">暂无会话数据</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {data && data.total > limit && (
            <div className="flex items-center justify-between">
              <p className="text-sm text-gray-500">
                共 {data.total} 个会话，当前显示 {offset + 1} - {Math.min(offset + limit, data.total)}
              </p>
              <div className="flex gap-2">
                <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}
                  className="rounded-md border border-gray-300 px-3 py-1.5 text-sm disabled:opacity-50">上一页</button>
                <button type="button" disabled={offset + limit >= data.total} onClick={() => setOffset(offset + limit)}
                  className="rounded-md border border-gray-300 px-3 py-1.5 text-sm disabled:opacity-50">下一页</button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
