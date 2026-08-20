import { useCallback, useEffect, useState } from "react";
import { api } from "@/platform/http/api";
import type { Workspace, WorkspaceMember } from "@/shared/types";

export function WorkspaceListPage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Workspace | null>(null);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [loadingMembers, setLoadingMembers] = useState(false);
  const [memberError, setMemberError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await api.listWorkspaces();
      setWorkspaces(result.workspaces);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    queueMicrotask(() => void load());
  }, [load]);

  const handleSelect = async (ws: Workspace) => {
    setSelected(ws);
    setMemberError("");
    setLoadingMembers(true);
    try {
      const list = await api.listWorkspaceMembers(ws.id);
      setMembers(list);
    } catch (err) {
      setMemberError(err instanceof Error ? err.message : "加载成员失败");
    } finally {
      setLoadingMembers(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">工作区管理</h1>
        <p className="text-sm text-gray-500">管理工作区和成员</p>
      </div>

      {error && <div className="rounded bg-red-50 p-4 text-sm text-red-700">{error}</div>}
      {memberError && <div className="rounded bg-red-50 p-3 text-sm text-red-700">{memberError}</div>}

      <div className="grid gap-6 lg:grid-cols-2">
        <div>
          {loading && workspaces.length === 0 ? (
            <div className="py-12 text-center text-gray-500">加载中...</div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              {workspaces.map((ws) => (
                <button
                  key={ws.id}
                  type="button"
                  onClick={() => void handleSelect(ws)}
                  className={`cursor-pointer rounded-lg border p-5 text-left shadow-sm transition hover:border-blue-300 hover:shadow-md ${
                    selected?.id === ws.id ? "border-blue-300 bg-blue-50" : "border-gray-200 bg-white"
                  }`}
                >
                  <div className="flex items-start justify-between">
                    <div>
                      <h3 className="font-semibold text-gray-900">{ws.name}</h3>
                      <p className="mt-1 text-xs text-gray-500">/{ws.slug}</p>
                    </div>
                    <span
                      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-semibold ${
                        ws.status === "active" ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-600"
                      }`}
                    >
                      {ws.status}
                    </span>
                  </div>
                  <p className="mt-3 text-xs text-gray-400">创建于 {new Date(ws.created_at).toLocaleDateString("zh-CN")}</p>
                </button>
              ))}
              {workspaces.length === 0 && !loading && (
                <div className="col-span-full py-12 text-center text-gray-500">暂无工作区</div>
              )}
            </div>
          )}
        </div>

        <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold text-gray-700">成员列表</h2>
          {!selected ? (
            <p className="py-8 text-center text-sm text-gray-500">选择一个工作区查看成员</p>
          ) : loadingMembers ? (
            <p className="py-8 text-center text-sm text-gray-500">加载中...</p>
          ) : members.length === 0 ? (
            <p className="py-8 text-center text-sm text-gray-500">暂无成员</p>
          ) : (
            <div className="max-h-96 space-y-1 overflow-y-auto">
              {members.map((m) => (
                <div
                  key={m.user_id}
                  className="flex items-center justify-between rounded px-3 py-2 text-sm hover:bg-gray-50"
                >
                  <span className="font-medium text-gray-900">{m.user_id}</span>
                  <span className="text-xs text-gray-500">{m.member_type}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
