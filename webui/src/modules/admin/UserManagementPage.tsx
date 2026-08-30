import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, KeyRound, Pencil } from "lucide-react";
import { api } from "@/platform/http/api";
import type { RbacRole, UserListResponse, UserProfile } from "@/shared/types";
import { PasswordResetDialog } from "@/shared/ui/PasswordResetDialog";
import { TextInputDialog } from "@/shared/ui/TextInputDialog";

type PageItem = number | "ellipsis";

function getPageItems(currentPage: number, totalPages: number): PageItem[] {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, index) => index + 1);
  if (currentPage <= 4) return [1, 2, 3, 4, 5, "ellipsis", totalPages];
  if (currentPage >= totalPages - 3) return [1, "ellipsis", totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages];
  return [1, "ellipsis", currentPage - 1, currentPage, currentPage + 1, "ellipsis", totalPages];
}

export interface UserManagementPageProps {
  onShellRefresh?: () => Promise<void>;
  refreshToken?: number;
}

export function UserManagementPage({ onShellRefresh, refreshToken = 0 }: UserManagementPageProps) {
  const [data, setData] = useState<UserListResponse | null>(null);
  const [roles, setRoles] = useState<RbacRole[]>([]);
  const [loading, setLoading] = useState(true);
  const [rolesLoading, setRolesLoading] = useState(true);
  const [actionPending, setActionPending] = useState(false);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [offset, setOffset] = useState(0);
  const limit = 12;
  const [pageInput, setPageInput] = useState("1");
  const [selected, setSelected] = useState<UserProfile | null>(null);
  const [displayNameTarget, setDisplayNameTarget] = useState<UserProfile | null>(null);
  const [passwordTarget, setPasswordTarget] = useState<UserProfile | null>(null);
  const [selectedRoles, setSelectedRoles] = useState<string[]>([]);
  const [userRolesLoading, setUserRolesLoading] = useState(false);
  const [form, setForm] = useState({ username: "", display_name: "", password: "" });
  const [createRoles, setCreateRoles] = useState<string[]>([]);
  const requestVersion = useRef(0);
  const userRolesRequestVersion = useRef(0);
  const load = useCallback(async () => {
    const requestId = ++requestVersion.current;
    setLoading(true); setError("");
    try {
      const result = await api.listUsers(offset, limit, statusFilter || undefined, search || undefined, includeDeleted);
      if (requestId !== requestVersion.current) return;
      setData(result);
    } catch (err) {
      if (requestId === requestVersion.current) setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      if (requestId === requestVersion.current) setLoading(false);
    }
  }, [includeDeleted, limit, offset, search, statusFilter]);

  const loadRoles = useCallback(async () => {
    setRolesLoading(true);
    try {
      setRoles((await api.listRoles()).items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "角色加载失败");
    } finally {
      setRolesLoading(false);
    }
  }, []);

  const activeRoles = roles.filter((role) => role.status === "active");

  useEffect(() => { queueMicrotask(() => void load()); }, [load, refreshToken]);
  useEffect(() => { queueMicrotask(() => void loadRoles()); }, [loadRoles, refreshToken]);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const next = searchInput.trim();
      setSearch((current) => current === next ? current : next);
      setOffset(0);
      setPageInput("1");
    }, 300);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  const selectUser = async (user: UserProfile) => {
    const requestId = ++userRolesRequestVersion.current;
    setSelected(user); setSelectedRoles([]); setActionError(""); setUserRolesLoading(true);
    try {
      const result = await api.getUserRoles(user.id);
      if (requestId === userRolesRequestVersion.current) setSelectedRoles(result.role_codes);
    } catch (err) {
      if (requestId === userRolesRequestVersion.current) setActionError(err instanceof Error ? err.message : "加载角色失败");
    } finally {
      if (requestId === userRolesRequestVersion.current) setUserRolesLoading(false);
    }
  };

  const run = async (action: () => Promise<unknown>, successMessage?: string) => {
    setActionError(""); setActionMessage(""); setActionPending(true);
    try {
      await action();
      await load();
      if (successMessage) setActionMessage(successMessage);
    } catch (err) { setActionError(err instanceof Error ? err.message : "操作失败"); }
    finally { setActionPending(false); }
  };

  const create = async () => {
    const activeCodes = new Set(activeRoles.map((role) => role.code));
    const selectedActiveRoles = createRoles.filter((code) => activeCodes.has(code));
    await run(async () => {
      await api.createUser({ ...form, role_codes: selectedActiveRoles });
      setForm({ username: "", display_name: "", password: "" });
      setCreateRoles([]);
    }, "用户已创建");
  };

  const updateDisplayName = (displayName: string) => {
    const target = displayNameTarget;
    setDisplayNameTarget(null);
    if (!target || displayName === target.display_name) return;
    void run(() => api.updateUser(target.id, { display_name: displayName }), "显示名已更新");
  };

  const resetPassword = (password: string) => {
    const target = passwordTarget;
    setPasswordTarget(null);
    if (!target) return;
    void run(() => api.resetUserPassword(target.id, password), "密码已重置，原有登录会话已失效");
  };

  const toggleRole = (code: string) => setSelectedRoles((current) => current.includes(code) ? current.filter((item) => item !== code) : [...current, code]);
  const toggleCreateRole = (code: string) => setCreateRoles((current) => current.includes(code) ? current.filter((item) => item !== code) : [...current, code]);
  const totalPages = data ? Math.max(1, Math.ceil(data.total / limit)) : 1;
  const currentPage = data ? Math.min(totalPages, Math.floor(offset / limit) + 1) : 1;
  const pageItems = getPageItems(currentPage, totalPages);
  const jumpToPage = () => {
    const requestedPage = Number.parseInt(pageInput, 10);
    if (!Number.isFinite(requestedPage)) {
      setPageInput(String(currentPage));
      return;
    }
    const targetPage = Math.min(totalPages, Math.max(1, requestedPage));
    setOffset((targetPage - 1) * limit);
    setPageInput(String(targetPage));
  };

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-white px-5 py-4 shadow-sm sm:flex-row sm:items-center sm:justify-between"><div><h1 className="text-2xl font-bold tracking-tight text-slate-900">用户管理</h1><p className="mt-1 text-sm text-slate-500">账户、生命周期、角色和会话安全</p></div><div className="flex items-center gap-2 text-sm text-slate-500"><span className="rounded-full bg-blue-50 px-3 py-1 font-medium text-blue-700">服务端分页</span><span>每页 12 条</span></div></header>
      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-4 flex items-center justify-between"><div><h2 className="text-sm font-semibold text-slate-900">创建用户</h2><p className="mt-1 text-xs text-slate-500">新用户默认使用游客角色，可在下方选择其他固定角色</p></div><span className="text-xs text-slate-400">必填项</span></div>
        <div className="grid gap-3 md:grid-cols-4">
          <input className="rounded border px-3 py-2 text-sm" placeholder="用户名" value={form.username} disabled={actionPending} onChange={(e) => setForm({ ...form, username: e.target.value })} />
          <input className="rounded border px-3 py-2 text-sm" placeholder="显示名称" value={form.display_name} disabled={actionPending} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          <input className="rounded border px-3 py-2 text-sm" type="password" placeholder="初始密码（至少 8 位）" value={form.password} disabled={actionPending} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          <button type="button" onClick={() => void create()} disabled={actionPending || form.username.trim().length < 3 || form.display_name.trim().length < 1 || form.password.length < 8} className="rounded bg-blue-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50">{actionPending ? "处理中…" : createRoles.some((code) => activeRoles.some((role) => role.code === code)) ? "创建并分配所选角色" : "创建并赋予游客角色"}</button>
        </div>
        <div className="mt-3 flex flex-wrap gap-3">
          {activeRoles.map((role) => <label key={role.code} className="flex items-center gap-2 rounded border px-3 py-1.5 text-sm"><input type="checkbox" disabled={actionPending || rolesLoading} checked={createRoles.includes(role.code)} onChange={() => toggleCreateRole(role.code)} />{role.name}（{role.code}）</label>)}
          {!activeRoles.length && <span className="text-xs text-gray-400">{rolesLoading ? "正在加载角色…" : "未选择角色时默认赋予游客角色"}</span>}
        </div>
      </section>
      <div className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-slate-50/70 p-4 sm:flex-row sm:items-center">
        <input className="min-w-64 flex-1 rounded border px-3 py-2 text-sm" placeholder="搜索用户名或显示名称" value={searchInput} onChange={(e) => setSearchInput(e.target.value)} />
        <select className="rounded border px-3 py-2 text-sm" value={statusFilter} disabled={actionPending} onChange={(e) => { setStatusFilter(e.target.value); setOffset(0); setPageInput("1"); }}><option value="">全部状态</option><option value="active">活跃</option><option value="disabled">已禁用</option><option value="locked">已锁定</option><option value="deleted">已软删</option></select>
        <label className="flex items-center gap-2 rounded border px-3 py-2 text-sm"><input type="checkbox" disabled={actionPending} checked={includeDeleted} onChange={(e) => { setIncludeDeleted(e.target.checked); setOffset(0); setPageInput("1"); }} />包含软删</label>
      </div>
      {error && <div className="rounded bg-red-50 p-4 text-sm text-red-700">{error}</div>}
      {actionError && <div className="rounded bg-red-50 p-3 text-sm text-red-700">{actionError}</div>}
      {actionMessage && <div className="rounded bg-green-50 p-3 text-sm text-green-700">{actionMessage}</div>}
      <div className="relative overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
        {loading && data && <div className="absolute inset-0 z-10 flex items-start justify-center bg-white/70 pt-16 text-sm text-slate-500 backdrop-blur-[1px]" role="status">正在加载当前页...</div>}
        {loading && !data ? <div className="py-12 text-center text-gray-500">加载用户中...</div> : <table aria-busy={loading} className="min-w-full divide-y divide-gray-200"><thead className="bg-gray-50"><tr>{["用户名", "显示名称", "状态", "最后登录", "创建时间", "操作"].map((item) => <th key={item} className="px-4 py-3 text-left text-xs font-medium text-gray-500">{item}</th>)}</tr></thead><tbody className="divide-y divide-gray-200">
          {(data?.users ?? []).map((user) => <tr key={user.id} className="hover:bg-gray-50"><td className="px-4 py-3 text-sm font-medium text-gray-900">{user.username}</td><td className="px-4 py-3 text-sm text-gray-600">{user.display_name}</td><td className="px-4 py-3 text-sm">{user.deleted_at ? "已软删" : user.status}</td><td className="px-4 py-3 text-sm text-gray-500">{user.last_login_at ? new Date(user.last_login_at).toLocaleString("zh-CN") : "从未登录"}</td><td className="px-4 py-3 text-sm text-gray-500">{new Date(user.created_at).toLocaleDateString("zh-CN")}</td><td className="space-x-2 whitespace-nowrap px-4 py-3 text-right text-xs"><button type="button" disabled={actionPending || userRolesLoading} className="inline-flex items-center gap-1 text-blue-700 disabled:opacity-50" onClick={() => void selectUser(user)}>角色</button>{!user.deleted_at && <><button type="button" disabled={actionPending || userRolesLoading} className="inline-flex items-center gap-1 text-slate-700 disabled:opacity-50" title="编辑显示名" onClick={() => setDisplayNameTarget(user)}><Pencil size={13} />编辑</button><button type="button" disabled={actionPending || userRolesLoading} className="inline-flex items-center gap-1 text-violet-700 disabled:opacity-50" title="重置密码" onClick={() => setPasswordTarget(user)}><KeyRound size={13} />重置密码</button></>}<button type="button" disabled={actionPending || userRolesLoading} className="inline-flex items-center gap-1 text-purple-700 disabled:opacity-50" onClick={() => void run(() => api.revokeUserSessions(user.id), "会话已撤销")}>撤销会话</button>{user.deleted_at ? <button type="button" disabled={actionPending || userRolesLoading} className="text-green-700 disabled:opacity-50" onClick={() => void run(() => api.restoreUser(user.id), "用户已恢复")}>恢复</button> : <><button type="button" disabled={actionPending || userRolesLoading} className="text-amber-700 disabled:opacity-50" onClick={() => void run(() => user.status === "active" ? api.disableUser(user.id) : api.enableUser(user.id), user.status === "active" ? "用户已禁用" : "用户已启用")}>{user.status === "active" ? "禁用" : "启用"}</button><button type="button" disabled={actionPending || userRolesLoading} className="text-red-700 disabled:opacity-50" onClick={() => { if (confirm(`软删用户 ${user.username}？`)) void run(() => api.deleteUser(user.id), "用户已软删"); }}>软删</button></>}</td></tr>)}
          {!data?.users.length && <tr><td colSpan={6} className="px-4 py-10 text-center text-sm text-gray-500">暂无用户</td></tr>}
        </tbody></table>}
      </div>
      {data && <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-gray-500"><span>共 {data.total} 个用户 · 第 {currentPage}/{totalPages} 页 · 每页 12 条</span><div className="flex flex-wrap items-center justify-end gap-2"><button type="button" aria-label="上一页" disabled={currentPage <= 1 || actionPending || loading} onClick={() => { const targetPage = currentPage - 1; setOffset((targetPage - 1) * limit); setPageInput(String(targetPage)); }} className="inline-flex items-center gap-1 rounded border px-2.5 py-1 disabled:opacity-50"><ChevronLeft size={14} />上一页</button>{pageItems.map((item, index) => item === "ellipsis" ? <span key={`ellipsis-${index}`} className="px-1" aria-hidden="true">...</span> : <button key={item} type="button" aria-label={`第 ${item} 页`} aria-current={item === currentPage ? "page" : undefined} disabled={actionPending || loading || item === currentPage} onClick={() => { setOffset((item - 1) * limit); setPageInput(String(item)); }} className={`min-w-8 rounded border px-2.5 py-1 disabled:opacity-100 ${item === currentPage ? "border-blue-600 bg-blue-600 text-white" : "hover:bg-gray-50 disabled:cursor-default"}`}>{item}</button>)}<button type="button" aria-label="下一页" disabled={currentPage >= totalPages || actionPending || loading} onClick={() => { const targetPage = currentPage + 1; setOffset((targetPage - 1) * limit); setPageInput(String(targetPage)); }} className="inline-flex items-center gap-1 rounded border px-2.5 py-1 disabled:opacity-50">下一页<ChevronRight size={14} /></button><form className="flex items-center gap-1.5" onSubmit={(event) => { event.preventDefault(); jumpToPage(); }}><label htmlFor="user-page-jump">跳转</label><input id="user-page-jump" className="w-16 rounded border px-2 py-1 text-center" type="number" min={1} max={totalPages} value={pageInput} disabled={actionPending || loading} onChange={(event) => setPageInput(event.target.value)} /><button type="submit" disabled={actionPending || loading} className="rounded border px-2.5 py-1 hover:bg-gray-50 disabled:opacity-50">确定</button>{loading && <span className="text-xs text-gray-400">加载中...</span>}</form></div></div>}
      {selected && <section className="rounded-lg border border-blue-200 bg-blue-50 p-4"><div className="flex items-center justify-between"><h2 className="font-semibold text-gray-900">{selected.username} 的角色</h2><button type="button" className="text-sm text-gray-500" disabled={actionPending || userRolesLoading} onClick={() => setSelected(null)}>关闭</button></div>{userRolesLoading ? <div className="py-4 text-sm text-gray-500">加载角色中...</div> : <><div className="mt-3 flex flex-wrap gap-3">{activeRoles.map((role) => <label key={role.code} className="flex items-center gap-2 rounded bg-white px-3 py-2 text-sm"><input type="checkbox" disabled={actionPending} checked={selectedRoles.includes(role.code)} onChange={() => toggleRole(role.code)} />{role.name}（{role.code}）</label>)}</div><button type="button" disabled={actionPending} className="mt-3 rounded bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50" onClick={() => void run(async () => { await api.replaceUserRoles(selected.id, selectedRoles); setSelected(null); await onShellRefresh?.(); }, "角色已更新，旧会话已撤销")}>{actionPending ? "保存中…" : "保存角色并撤销旧会话"}</button></>}</section>}
      <TextInputDialog key={displayNameTarget?.id ?? "display-name-dialog"} open={displayNameTarget !== null} title="编辑显示名" description={`修改 @${displayNameTarget?.username ?? "用户"} 在平台中显示的名称。`} label="显示名称" initialValue={displayNameTarget?.display_name ?? ""} placeholder="请输入显示名称" confirmLabel="保存修改" maxLength={128} onClose={() => setDisplayNameTarget(null)} onConfirm={updateDisplayName} />
      <PasswordResetDialog key={passwordTarget?.id ?? "password-dialog"} open={passwordTarget !== null} username={passwordTarget?.username ?? "用户"} onClose={() => setPasswordTarget(null)} onConfirm={resetPassword} />
    </div>
  );
}
