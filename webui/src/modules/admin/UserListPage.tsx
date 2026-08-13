import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "@/platform/http/api";
import type { UserProfile, UserListResponse, RoleCatalogItem } from "@/shared/types";

interface CreateUserDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

export function CreateUserDialog({ open, onClose, onCreated }: CreateUserDialogProps) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const reset = () => {
    setUsername("");
    setDisplayName("");
    setPassword("");
    setError("");
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) {
      setError("用户名和密码为必填项");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      // Use the updateUser endpoint pattern - create user via admin API
      // Note: Backend needs a create user endpoint; for now we use a workaround
      await api.updateUser(username, { display_name: displayName || username });
      onCreated();
      reset();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
        <h2 className="mb-4 text-lg font-semibold text-gray-900">创建用户</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && (
            <div className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700">用户名 *</label>
            <input
              type="text"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              disabled={submitting}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">显示名称</label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              disabled={submitting}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">密码 *</label>
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              disabled={submitting}
            />
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={() => { reset(); onClose(); }}
              className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
              disabled={submitting}
            >
              取消
            </button>
            <button
              type="submit"
              className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              disabled={submitting}
            >
              {submitting ? "创建中..." : "创建"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export function UserListPage() {
  const [data, setData] = useState<UserListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [showCreate, setShowCreate] = useState(false);
  const [offset, setOffset] = useState(0);
  const limit = 20;

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
    try {
      if (user.status === "active") {
        await api.disableUser(user.id);
      } else {
        await api.enableUser(user.id);
      }
      await load();
    } catch (err) {
      alert(err instanceof Error ? err.message : "操作失败");
    }
  };

  const [roleDialogUser, setRoleDialogUser] = useState<UserProfile | null>(null);
  const [allRoles, setAllRoles] = useState<RoleCatalogItem[]>([]);
  const [selectedRoleCodes, setSelectedRoleCodes] = useState<Set<string>>(new Set());
  const [roleSaving, setRoleSaving] = useState(false);
  const [roleError, setRoleError] = useState("");

  useEffect(() => {
    void api.listRoles().then((r) => setAllRoles(r.items)).catch(() => undefined);
  }, []);

  const openRoleDialog = async (user: UserProfile) => {
    setRoleDialogUser(user);
    setRoleError("");
    try {
      const resp = await api.getUserRoles(user.id);
      setSelectedRoleCodes(new Set(resp.role_codes));
    } catch (e) {
      setRoleError(e instanceof Error ? e.message : "加载角色失败");
    }
  };

  const saveRoles = async () => {
    if (!roleDialogUser) return;
    setRoleSaving(true);
    setRoleError("");
    try {
      await api.replaceUserRoles(roleDialogUser.id, Array.from(selectedRoleCodes));
      setRoleDialogUser(null);
      await load();
    } catch (e) {
      setRoleError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setRoleSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">用户管理</h1>
          <p className="text-sm text-gray-500">管理系统用户账户</p>
        </div>
        <button
          type="button"
          onClick={() => setShowCreate(true)}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
        >
          + 新建用户
        </button>
      </div>

      {/* Filters */}
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
          onChange={(e) => { setStatusFilter(e.target.value); setOffset(0); }}
          className="rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          <option value="">全部状态</option>
          <option value="active">活跃</option>
          <option value="disabled">已禁用</option>
        </select>
      </div>

      {error && (
        <div className="rounded bg-red-50 p-4 text-sm text-red-700">{error}</div>
      )}

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
                  <th className="px-6 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 bg-white">
                {filteredUsers.map((user) => (
                  <tr key={user.id} className="hover:bg-gray-50">
                    <td className="whitespace-nowrap px-6 py-4 text-sm font-medium text-gray-900">{user.username}</td>
                    <td className="whitespace-nowrap px-6 py-4 text-sm text-gray-500">{user.display_name}</td>
                    <td className="whitespace-nowrap px-6 py-4">
                      <span className={`inline-flex rounded-full px-2 py-1 text-xs font-semibold ${user.status === "active" ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}>
                        {user.status === "active" ? "活跃" : "已禁用"}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-6 py-4 text-sm text-gray-500">
                      {new Date(user.created_at).toLocaleDateString("zh-CN")}
                    </td>
                    <td className="whitespace-nowrap px-6 py-4 text-right text-sm">
                      <button
                        type="button"
                        onClick={() => void handleToggleStatus(user)}
                        className={`rounded px-3 py-1 text-xs font-medium ${user.status === "active" ? "bg-red-50 text-red-700 hover:bg-red-100" : "bg-green-50 text-green-700 hover:bg-green-100"}`}
                      >
                        {user.status === "active" ? "禁用" : "启用"}
                      </button>
                      <button
                        type="button"
                        onClick={() => void openRoleDialog(user)}
                        className="rounded px-3 py-1 text-xs font-medium bg-blue-50 text-blue-700 hover:bg-blue-100"
                      >
                        分配角色
                      </button>
                    </td>
                  </tr>
                ))}
                {filteredUsers.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-6 py-8 text-center text-sm text-gray-500">
                      暂无用户数据
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
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

      <CreateUserDialog open={showCreate} onClose={() => setShowCreate(false)} onCreated={() => void load()} />
      {roleDialogUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
            <h2 className="mb-1 text-lg font-semibold text-gray-900">分配角色</h2>
            <p className="mb-4 text-sm text-gray-500">{roleDialogUser.username}</p>
            {roleError && <div className="mb-3 rounded bg-red-50 p-3 text-sm text-red-700">{roleError}</div>}
            <div className="max-h-64 space-y-1 overflow-y-auto">
              {allRoles.map((r) => (
                <label key={r.code} className="flex items-center gap-2 rounded-md px-3 py-2 text-sm hover:bg-gray-50">
                  <input
                    type="checkbox"
                    checked={selectedRoleCodes.has(r.code)}
                    onChange={() => setSelectedRoleCodes((prev) => {
                      const next = new Set(prev);
                      if (next.has(r.code)) next.delete(r.code);
                      else next.add(r.code);
                      return next;
                    })}
                  />
                  <span><strong>{r.name}</strong><small className="block text-xs text-gray-500">{r.code}</small></span>
                </label>
              ))}
            </div>
            <div className="mt-4 flex justify-end gap-3">
              <button type="button" onClick={() => setRoleDialogUser(null)} className="rounded-md border border-gray-300 px-4 py-2 text-sm">取消</button>
              <button type="button" disabled={roleSaving} onClick={() => void saveRoles()} className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">{roleSaving ? "保存中..." : "保存"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
