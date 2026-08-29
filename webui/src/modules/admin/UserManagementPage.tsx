import { useCallback, useEffect, useState } from "react";
import { api } from "@/platform/http/api";
import type { RbacRole, UserListResponse, UserProfile } from "@/shared/types";

export function UserManagementPage() {
  const [data, setData] = useState<UserListResponse | null>(null);
  const [roles, setRoles] = useState<RbacRole[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<UserProfile | null>(null);
  const [selectedRoles, setSelectedRoles] = useState<string[]>([]);
  const [form, setForm] = useState({ username: "", display_name: "", password: "" });
  const [createRoles, setCreateRoles] = useState<string[]>([]);
  const limit = 20;

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const [result, roleResult] = await Promise.all([
        api.listUsers(offset, limit, statusFilter || undefined, search || undefined, includeDeleted),
        roles.length ? Promise.resolve({ items: roles }) : api.listRoles(),
      ]);
      setData(result); if (!roles.length) setRoles(roleResult.items);
    } catch (err) { setError(err instanceof Error ? err.message : "加载失败"); }
    finally { setLoading(false); }
  }, [includeDeleted, offset, roles, search, statusFilter]);

  useEffect(() => { queueMicrotask(() => void load()); }, [load]);

  const selectUser = async (user: UserProfile) => {
    setSelected(user); setActionError("");
    try { setSelectedRoles((await api.getUserRoles(user.id)).role_codes); }
    catch (err) { setActionError(err instanceof Error ? err.message : "加载角色失败"); }
  };

  const run = async (action: () => Promise<unknown>) => {
    setActionError("");
    try { await action(); await load(); }
    catch (err) { setActionError(err instanceof Error ? err.message : "操作失败"); }
  };

  const create = async () => {
    await run(async () => {
      await api.createUser({ ...form, role_codes: createRoles });
      setForm({ username: "", display_name: "", password: "" });
      setCreateRoles([]);
    });
  };

  const toggleRole = (code: string) => setSelectedRoles((current) => current.includes(code) ? current.filter((item) => item !== code) : [...current, code]);
  const toggleCreateRole = (code: string) => setCreateRoles((current) => current.includes(code) ? current.filter((item) => item !== code) : [...current, code]);

  return (
    <div className="space-y-6">
      <div><h1 className="text-2xl font-bold text-gray-900">用户管理</h1><p className="text-sm text-gray-500">账户、生命周期、默认 RBAC 角色和会话安全均由服务端落库。</p></div>
      <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <h2 className="mb-3 text-sm font-semibold text-gray-800">创建用户</h2>
        <div className="grid gap-3 md:grid-cols-4">
          <input className="rounded border px-3 py-2 text-sm" placeholder="用户名" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
          <input className="rounded border px-3 py-2 text-sm" placeholder="显示名称" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          <input className="rounded border px-3 py-2 text-sm" type="password" placeholder="初始密码（至少 8 位）" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          <button type="button" onClick={() => void create()} disabled={form.username.length < 3 || form.display_name.length < 1 || form.password.length < 8} className="rounded bg-blue-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50">{createRoles.length ? "创建并分配所选角色" : "创建并赋予游客角色"}</button>
        </div>
        <div className="mt-3 flex flex-wrap gap-3">
          {roles.map((role) => <label key={role.code} className="flex items-center gap-2 rounded border px-3 py-1.5 text-sm"><input type="checkbox" checked={createRoles.includes(role.code)} onChange={() => toggleCreateRole(role.code)} />{role.name}（{role.code}）</label>)}
          {!roles.length && <span className="text-xs text-gray-400">未选择角色时默认赋予游客角色</span>}
        </div>
      </section>
      <div className="flex flex-wrap gap-3">
        <input className="min-w-64 flex-1 rounded border px-3 py-2 text-sm" placeholder="搜索用户名或显示名称" value={search} onChange={(e) => { setSearch(e.target.value); setOffset(0); }} />
        <select className="rounded border px-3 py-2 text-sm" value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setOffset(0); }}><option value="">全部状态</option><option value="active">活跃</option><option value="disabled">已禁用</option><option value="locked">已锁定</option><option value="deleted">已软删</option></select>
        <label className="flex items-center gap-2 rounded border px-3 py-2 text-sm"><input type="checkbox" checked={includeDeleted} onChange={(e) => { setIncludeDeleted(e.target.checked); setOffset(0); }} />包含软删</label>
      </div>
      {error && <div className="rounded bg-red-50 p-4 text-sm text-red-700">{error}</div>}
      {actionError && <div className="rounded bg-red-50 p-3 text-sm text-red-700">{actionError}</div>}
      <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
        {loading && !data ? <div className="py-12 text-center text-gray-500">加载中...</div> : <table className="min-w-full divide-y divide-gray-200"><thead className="bg-gray-50"><tr>{["用户名", "显示名称", "状态", "最后登录", "创建时间", "操作"].map((item) => <th key={item} className="px-4 py-3 text-left text-xs font-medium text-gray-500">{item}</th>)}</tr></thead><tbody className="divide-y divide-gray-200">
          {(data?.users ?? []).map((user) => <tr key={user.id} className="hover:bg-gray-50"><td className="px-4 py-3 text-sm font-medium text-gray-900">{user.username}</td><td className="px-4 py-3 text-sm text-gray-600">{user.display_name}</td><td className="px-4 py-3 text-sm">{user.deleted_at ? "已软删" : user.status}</td><td className="px-4 py-3 text-sm text-gray-500">{user.last_login_at ? new Date(user.last_login_at).toLocaleString("zh-CN") : "从未登录"}</td><td className="px-4 py-3 text-sm text-gray-500">{new Date(user.created_at).toLocaleDateString("zh-CN")}</td><td className="space-x-2 whitespace-nowrap px-4 py-3 text-right text-xs"><button type="button" className="text-blue-700" onClick={() => void selectUser(user)}>角色</button><button type="button" className="text-purple-700" onClick={() => void run(() => api.revokeUserSessions(user.id))}>撤销会话</button>{user.deleted_at ? <button type="button" className="text-green-700" onClick={() => void run(() => api.restoreUser(user.id))}>恢复</button> : <><button type="button" className="text-amber-700" onClick={() => void run(() => user.status === "active" ? api.disableUser(user.id) : api.enableUser(user.id))}>{user.status === "active" ? "禁用" : "启用"}</button><button type="button" className="text-red-700" onClick={() => { if (confirm(`软删用户 ${user.username}？`)) void run(() => api.deleteUser(user.id)); }}>软删</button></>}</td></tr>)}
          {!data?.users.length && <tr><td colSpan={6} className="px-4 py-10 text-center text-sm text-gray-500">暂无用户</td></tr>}
        </tbody></table>}
      </div>
      {data && <div className="flex items-center justify-between text-sm text-gray-500"><span>共 {data.total} 个用户</span><div className="space-x-2"><button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))} className="rounded border px-3 py-1 disabled:opacity-50">上一页</button><button type="button" disabled={offset + limit >= data.total} onClick={() => setOffset(offset + limit)} className="rounded border px-3 py-1 disabled:opacity-50">下一页</button></div></div>}
      {selected && <section className="rounded-lg border border-blue-200 bg-blue-50 p-4"><div className="flex items-center justify-between"><h2 className="font-semibold text-gray-900">{selected.username} 的角色</h2><button type="button" className="text-sm text-gray-500" onClick={() => setSelected(null)}>关闭</button></div><div className="mt-3 flex flex-wrap gap-3">{roles.map((role) => <label key={role.code} className="flex items-center gap-2 rounded bg-white px-3 py-2 text-sm"><input type="checkbox" checked={selectedRoles.includes(role.code)} onChange={() => toggleRole(role.code)} />{role.name}（{role.code}）</label>)}</div><button type="button" className="mt-3 rounded bg-blue-600 px-4 py-2 text-sm text-white" onClick={() => void run(async () => { await api.replaceUserRoles(selected.id, selectedRoles); setSelected(null); })}>保存角色并撤销旧会话</button></section>}
    </div>
  );
}
