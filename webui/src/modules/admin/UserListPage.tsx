import { useCallback, useEffect, useState } from "react";
import { api } from "@/platform/http/api";
import type { UserListResponse, UserProfile } from "@/shared/types";

// 四个内置角色（与后端 core/rbac.py 的 ROLE_PERMISSIONS 对齐）。
const ROLE_OPTIONS: { code: string; label: string }[] = [
  { code: "guest", label: "游客" },
  { code: "student", label: "学生" },
  { code: "teacher", label: "教师" },
  { code: "developer", label: "开发者" },
];
const ROLE_LABELS: Record<string, string> = Object.fromEntries(
  ROLE_OPTIONS.map((r) => [r.code, r.label]),
);

// 列表、搜索、状态过滤、启用/禁用以及角色分配（PUT /users/{id}/roles）。
export function UserListPage() {
  const [data, setData] = useState<UserListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const limit = 20;
  const [editingRolesFor, setEditingRolesFor] = useState<string | null>(null);
  const [draftRoles, setDraftRoles] = useState<string[]>([]);
  const [savingRoles, setSavingRoles] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await api.listUsers(offset, limit, statusFilter || undefined);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [offset, statusFilter]);

  useEffect(() => {
    queueMicrotask(() => void load());
  }, [load]);

  const filteredUsers = data?.users.filter(
    (u) =>
      !search ||
      u.username.toLowerCase().includes(search.toLowerCase()) ||
      u.display_name.toLowerCase().includes(search.toLowerCase()),
  ) ?? [];

  const handleToggleStatus = async (user: UserProfile) => {
    setActionError("");
    try {
      if (user.status === "active") {
        await api.disableUser(user.id);
      } else {
        await api.enableUser(user.id);
      }
      await load();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "操作失败");
    }
  };

  const startEditRoles = (user: UserProfile) => {
    setActionError("");
    setEditingRolesFor(user.id);
    setDraftRoles([...(user.roles ?? [])]);
  };

  const toggleDraftRole = (code: string) => {
    setDraftRoles((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code],
    );
  };

  const saveRoles = async (user: UserProfile) => {
    if (draftRoles.length === 0) {
      setActionError("至少需要选择一个角色");
      return;
    }
    setSavingRoles(true);
    setActionError("");
    try {
      await api.replaceUserRoles(user.id, draftRoles);
      setEditingRolesFor(null);
      await load();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "角色更新失败");
    } finally {
      setSavingRoles(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">用户管理</h1>
        <p className="text-sm text-gray-500">管理系统用户账户</p>
      </div>

      <div className="flex gap-3">
        <input
          type="text"
          placeholder="搜索用户名或显示名称..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <select
          value={statusFilter}
          onChange={(e) => {
            setStatusFilter(e.target.value);
            setOffset(0);
          }}
          className="rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          <option value="">全部状态</option>
          <option value="active">活跃</option>
          <option value="disabled">已禁用</option>
        </select>
      </div>

      {error && <div className="rounded bg-red-50 p-4 text-sm text-red-700">{error}</div>}
      {actionError && <div className="rounded bg-red-50 p-3 text-sm text-red-700">{actionError}</div>}

      {loading && !data ? (
        <div className="py-12 text-center text-gray-500">加载中...</div>
      ) : (
        <>
          <div className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">用户名</th>
                  <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">显示名称</th>
                  <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">状态</th>
                  <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">创建时间</th>
                  <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">角色</th>
                  <th className="px-6 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 bg-white">
                {filteredUsers.map((user) => (
                  <tr key={user.id} className="hover:bg-gray-50">
                    <td className="whitespace-nowrap px-6 py-4 text-sm font-medium text-gray-900">{user.username}</td>
                    <td className="whitespace-nowrap px-6 py-4 text-sm text-gray-500">{user.display_name}</td>
                    <td className="whitespace-nowrap px-6 py-4">
                      <span
                        className={`inline-flex rounded-full px-2 py-1 text-xs font-semibold ${
                          user.status === "active" ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"
                        }`}
                      >
                        {user.status === "active" ? "活跃" : "已禁用"}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-6 py-4 text-sm text-gray-500">
                      {new Date(user.created_at).toLocaleDateString("zh-CN")}
                    </td>
                    <td className="px-6 py-4">
                      {editingRolesFor === user.id ? (
                        <div className="flex flex-wrap items-center gap-3">
                          {ROLE_OPTIONS.map((r) => (
                            <label key={r.code} className="inline-flex items-center gap-1 text-xs text-gray-700">
                              <input
                                type="checkbox"
                                checked={draftRoles.includes(r.code)}
                                onChange={() => toggleDraftRole(r.code)}
                              />
                              {r.label}
                            </label>
                          ))}
                        </div>
                      ) : (
                        <div className="flex flex-wrap gap-1">
                          {(user.roles ?? []).length === 0 ? (
                            <span className="text-xs text-gray-400">—</span>
                          ) : (
                            (user.roles ?? []).map((code) => (
                              <span
                                key={code}
                                className="inline-flex rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-800"
                              >
                                {ROLE_LABELS[code] ?? code}
                              </span>
                            ))
                          )}
                        </div>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-6 py-4 text-right text-sm">
                      <div className="flex justify-end gap-2">
                        {editingRolesFor === user.id ? (
                          <>
                            <button
                              type="button"
                              disabled={savingRoles}
                              onClick={() => void saveRoles(user)}
                              className="rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                            >
                              {savingRoles ? "保存中..." : "保存角色"}
                            </button>
                            <button
                              type="button"
                              onClick={() => setEditingRolesFor(null)}
                              className="rounded bg-gray-100 px-3 py-1 text-xs font-medium text-gray-700 hover:bg-gray-200"
                            >
                              取消
                            </button>
                          </>
                        ) : (
                          <button
                            type="button"
                            onClick={() => startEditRoles(user)}
                            className="rounded bg-indigo-50 px-3 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-100"
                          >
                            分配角色
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => void handleToggleStatus(user)}
                          className={`rounded px-3 py-1 text-xs font-medium ${
                            user.status === "active" ? "bg-red-50 text-red-700 hover:bg-red-100" : "bg-green-50 text-green-700 hover:bg-green-100"
                          }`}
                        >
                          {user.status === "active" ? "禁用" : "启用"}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
                {filteredUsers.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-6 py-8 text-center text-sm text-gray-500">
                      暂无用户数据
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {data && data.total > limit && (
            <div className="flex items-center justify-between">
              <p className="text-sm text-gray-500">
                共 {data.total} 个用户，当前显示 {offset + 1} - {Math.min(offset + limit, data.total)}
              </p>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - limit))}
                  className="rounded-md border border-gray-300 px-3 py-1.5 text-sm disabled:opacity-50"
                >
                  上一页
                </button>
                <button
                  type="button"
                  disabled={offset + limit >= data.total}
                  onClick={() => setOffset(offset + limit)}
                  className="rounded-md border border-gray-300 px-3 py-1.5 text-sm disabled:opacity-50"
                >
                  下一页
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
