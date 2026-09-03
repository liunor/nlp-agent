import { useCallback, useEffect, useRef, useState } from "react";
import { Ban, Check, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, KeyRound, Pencil, RefreshCw, Search, ShieldCheck, Trash2, Unlock } from "lucide-react";
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

function statusMeta(status: UserProfile["status"]) {
  return status === "active"
    ? { label: "活跃", className: "bg-emerald-50 text-emerald-700" }
    : status === "locked"
      ? { label: "已锁定", className: "bg-amber-50 text-amber-700" }
      : { label: "已禁用", className: "bg-rose-50 text-rose-700" };
}

export interface UserManagementPageProps {
  onShellRefresh?: () => Promise<void>;
  refreshToken?: number;
}

export function UserManagementPage({ refreshToken = 0 }: UserManagementPageProps) {
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
  const limit = 8;
  const [pageInput, setPageInput] = useState("1");
  const [expandedUserId, setExpandedUserId] = useState<string | null>(null);
  const [createExpanded, setCreateExpanded] = useState(false);
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

  const updateVisibleUser = (userId: string, patch: Partial<UserProfile>) => {
    setData((current) => {
      if (!current) return current;
      const target = current.users.find((user) => user.id === userId);
      if (!target) return current;
      const next = { ...target, ...patch };
      if (!matchesCurrentView(next)) return { ...current, users: current.users.filter((user) => user.id !== userId), total: Math.max(0, current.total - 1) };
      return { ...current, users: current.users.map((user) => user.id === userId ? next : user) };
    });
    setSelected((current) => current?.id === userId ? { ...current, ...patch } : current);
  };

  const run = async (action: () => Promise<unknown>, successMessage?: string, onSuccess?: (result: unknown) => void) => {
    setActionError(""); setActionMessage(""); setActionPending(true);
    try {
      const result = await action();
      onSuccess?.(result);
      if (successMessage) setActionMessage(successMessage);
    } catch (err) { setActionError(err instanceof Error ? err.message : "操作失败"); }
    finally { setActionPending(false); }
  };

  const create = async () => {
    const activeCodes = new Set(activeRoles.map((role) => role.code));
    const selectedActiveRoles = createRoles.filter((code) => activeCodes.has(code));
    if (!window.confirm(`确认创建用户 ${form.username.trim()}？`)) return;
    await run(async () => {
      return api.createUser({ ...form, role_codes: selectedActiveRoles });
    }, "用户已创建", (result) => {
      setForm({ username: "", display_name: "", password: "" });
      setCreateRoles([]);
      if (result && typeof result === "object" && "id" in result && matchesCurrentView(result as UserProfile)) {
        setData((current) => current ? { ...current, users: offset === 0 ? [result as UserProfile, ...current.users].slice(0, limit) : current.users, total: current.total + 1 } : current);
      }
    });
  };

  const updateDisplayName = (displayName: string) => {
    const target = displayNameTarget;
    if (!target || displayName === target.display_name) return;
    if (!window.confirm(`确认将 ${target.username} 的显示名修改为“${displayName}”？`)) return;
    setDisplayNameTarget(null);
    void run(() => api.updateUser(target.id, { display_name: displayName }), "显示名已更新", (result) => {
      updateVisibleUser(target.id, result && typeof result === "object" ? result as Partial<UserProfile> : { display_name: displayName });
    });
  };

  const resetPassword = (password: string) => {
    const target = passwordTarget;
    if (!target) return;
    if (!window.confirm(`确认重置用户 ${target.username} 的密码？`)) return;
    setPasswordTarget(null);
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

  const matchesCurrentView = (user: UserProfile) => {
    if (user.deleted_at && !includeDeleted) return false;
    if (statusFilter === "deleted" && !user.deleted_at) return false;
    if (statusFilter && statusFilter !== "deleted" && user.status !== statusFilter) return false;
    if (search && !`${user.username} ${user.display_name}`.toLocaleLowerCase().includes(search.toLocaleLowerCase())) return false;
    return true;
  };

  const changeStatus = (user: UserProfile) => {
    const nextStatus = user.status === "active" ? "disabled" : "active";
    if (!window.confirm(`确认${nextStatus === "disabled" ? "禁用" : "启用"}用户 ${user.username}？`)) return;
    void run(() => nextStatus === "disabled" ? api.disableUser(user.id) : api.enableUser(user.id), nextStatus === "disabled" ? "用户已禁用" : "用户已启用", () => updateVisibleUser(user.id, { status: nextStatus }));
  };

  const deleteUser = (user: UserProfile) => {
    if (!window.confirm(`确认软删用户 ${user.username}？`)) return;
    void run(() => api.deleteUser(user.id), "用户已软删", () => {
      if (includeDeleted) updateVisibleUser(user.id, { deleted_at: new Date().toISOString() });
      else setData((current) => current ? { ...current, users: current.users.filter((item) => item.id !== user.id), total: Math.max(0, current.total - 1) } : current);
    });
  };

  const restoreUser = (user: UserProfile) => {
    if (!window.confirm(`确认恢复用户 ${user.username}？`)) return;
    void run(() => api.restoreUser(user.id), "用户已恢复", (result) => {
      const restored = result && typeof result === "object" ? result as Partial<UserProfile> : { deleted_at: null };
      updateVisibleUser(user.id, { ...restored, deleted_at: null });
    });
  };

  const revokeSessions = (user: UserProfile) => {
    if (!window.confirm(`确认撤销用户 ${user.username} 的全部会话？`)) return;
    void run(() => api.revokeUserSessions(user.id), "会话已撤销");
  };

  const saveRoles = () => {
    if (!selected) return;
    if (!window.confirm(`确认更新用户 ${selected.username} 的角色并撤销旧会话？`)) return;
    void run(() => api.replaceUserRoles(selected.id, selectedRoles), "角色已更新，旧会话已撤销", (result) => {
      const roleCodes = result && typeof result === "object" && "role_codes" in result ? (result as { role_codes: string[] }).role_codes : selectedRoles;
      updateVisibleUser(selected.id, { roles: roleCodes });
      setSelected(null);
    });
  };

  return (
    <div className="user-manage-page space-y-5">
      <header className="user-page-header flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between"><div><h1 className="text-2xl font-bold tracking-tight text-slate-900">用户管理</h1><p className="mt-1 text-sm text-slate-500">账户、生命周期、角色和会话安全</p></div><span className="text-xs text-slate-400">服务端分页 · 每页 {limit} 条</span></header>
      <section className={`user-search-card rounded-xl border border-slate-200 bg-white shadow-sm ${createExpanded ? "p-5" : "px-5 py-3"}`}>
        <button type="button" className="flex w-full items-center justify-between text-left" aria-expanded={createExpanded} onClick={() => setCreateExpanded((current) => !current)}><span><span className="flex items-center gap-2 text-sm font-semibold text-slate-900">{createExpanded ? <ChevronUp size={15} className="text-indigo-500" /> : <ChevronDown size={15} className="text-indigo-500" />}创建用户</span>{!createExpanded && <span className="mt-1 block text-xs text-slate-500">点击展开创建账号</span>}{createExpanded && <span className="mt-1 block text-xs text-slate-500">填写账号信息，可选分配已有角色</span>}</span><span className="text-xs text-slate-400">{createExpanded ? "收起" : "展开"}</span></button>
        {createExpanded && <>
        <div className="mt-4 grid gap-3 md:grid-cols-[repeat(3,minmax(0,1fr))_auto]">
          <input aria-label="用户名" className="h-10 rounded-lg border border-slate-200 px-3 text-sm outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100" placeholder="用户名" value={form.username} disabled={actionPending} onChange={(e) => setForm({ ...form, username: e.target.value })} />
          <input aria-label="显示名称" className="h-10 rounded-lg border border-slate-200 px-3 text-sm outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100" placeholder="显示名称" value={form.display_name} disabled={actionPending} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          <input aria-label="初始密码" className="h-10 rounded-lg border border-slate-200 px-3 text-sm outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100" type="password" placeholder="初始密码（至少 8 位）" value={form.password} disabled={actionPending} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          <button type="button" onClick={() => void create()} disabled={actionPending || form.username.trim().length < 3 || form.display_name.trim().length < 1 || form.password.length < 8} className="inline-flex h-10 items-center justify-center rounded-lg bg-indigo-600 px-4 text-sm font-semibold text-white shadow-sm transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50">{actionPending ? "处理中…" : createRoles.some((code) => activeRoles.some((role) => role.code === code)) ? "创建并分配所选角色" : "创建并赋予游客角色"}</button>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2"><span className="mr-1 text-xs text-slate-500">角色：</span>
          {activeRoles.map((role) => <label key={role.code} className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600"><input type="checkbox" disabled={actionPending || rolesLoading} checked={createRoles.includes(role.code)} onChange={() => toggleCreateRole(role.code)} />{role.name}</label>)}
          {!activeRoles.length && <span className="text-xs text-slate-400">{rolesLoading ? "正在加载角色…" : "未选择角色时默认赋予游客角色"}</span>}
        </div>
        </>}
      </section>
      <div className="user-filter-card flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-3 shadow-sm sm:flex-row sm:items-center">
        <label htmlFor="user-keyword" className="shrink-0 text-sm font-medium text-slate-600">搜索</label>
        <div className="min-w-64 flex-1"><input id="user-keyword" aria-label="搜索用户名或显示名称" className="h-10 w-full rounded-md border border-slate-200 px-3 text-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100" placeholder="用户名或显示名称" value={searchInput} onChange={(e) => setSearchInput(e.target.value)} /></div>
        <button type="button" className="inline-flex h-10 items-center justify-center gap-1.5 rounded-md bg-indigo-600 px-4 text-sm font-medium text-white hover:bg-indigo-700" onClick={() => { setSearch(searchInput.trim()); setOffset(0); setPageInput("1"); }}><Search size={15} />搜索</button>
        <select aria-label="状态筛选" className="h-10 rounded-lg border border-slate-200 px-3 text-sm text-slate-600 outline-none focus:border-indigo-400" value={statusFilter} disabled={actionPending} onChange={(e) => { setStatusFilter(e.target.value); setOffset(0); setPageInput("1"); }}><option value="">全部状态</option><option value="active">活跃</option><option value="disabled">已禁用</option><option value="locked">已锁定</option><option value="deleted">已软删</option></select>
        <label className="flex h-10 items-center gap-2 whitespace-nowrap rounded-lg border border-slate-200 px-3 text-sm text-slate-600"><input type="checkbox" disabled={actionPending} checked={includeDeleted} onChange={(e) => { setIncludeDeleted(e.target.checked); setOffset(0); setPageInput("1"); }} />包含软删账户</label>
      </div>
      {error && <div className="rounded bg-red-50 p-4 text-sm text-red-700">{error}</div>}
      {actionError && <div className="rounded bg-red-50 p-3 text-sm text-red-700">{actionError}</div>}
      {actionMessage && <div className="rounded bg-green-50 p-3 text-sm text-green-700">{actionMessage}</div>}
      <section className="user-table-card overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4"><div><h2 className="text-sm font-semibold text-slate-900">用户列表</h2><p className="mt-1 text-xs text-slate-500">管理账户状态、角色和登录会话</p></div><button type="button" aria-label="刷新用户列表" title="刷新用户列表" onClick={() => void load()} disabled={loading || actionPending} className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 px-3 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50"><RefreshCw className={loading ? "animate-spin" : ""} size={14} />刷新</button></div>
      <div className="relative overflow-x-auto">
        {loading && data && <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/60 backdrop-blur-[1px]" role="status" aria-live="polite"><div className="inline-flex items-center gap-2 rounded-full bg-white/95 px-3 py-1.5 text-xs text-slate-600 shadow-sm ring-1 ring-slate-200"><RefreshCw className="animate-spin text-indigo-500" size={13} />正在更新</div></div>}
        {loading && !data ? <table aria-busy={loading} className="min-w-[980px] w-full animate-pulse"><thead className="bg-slate-50"><tr>{["用户名", "显示名称", "状态", "最后登录", "创建时间", "操作"].map((item) => <th key={item} className="px-5 py-3 text-left text-xs font-semibold text-slate-500">{item}</th>)}</tr></thead><tbody className="divide-y divide-slate-100">{Array.from({ length: 6 }, (_, index) => <tr key={index}><td colSpan={6} className="px-5 py-4"><div className="grid grid-cols-[1.1fr_1.1fr_.6fr_1fr_1fr_2fr] items-center gap-5">{Array.from({ length: 6 }, (_, cell) => <span key={cell} className={`h-3 rounded bg-slate-200 ${cell === 5 ? "w-11/12" : cell === 2 ? "w-14" : "w-3/4"}`} />)}</div></td></tr>)}</tbody></table> : <table aria-busy={loading} className={`min-w-[980px] w-full transition-opacity ${loading ? "opacity-60" : ""}`}><thead className="bg-slate-50"><tr>{["用户名", "显示名称", "状态", "最后登录", "创建时间", "操作"].map((item) => <th key={item} className="px-5 py-3 text-left text-xs font-semibold text-slate-500">{item}</th>)}</tr></thead><tbody className="divide-y divide-slate-100">
          {(data?.users ?? []).map((user) => {
            const meta = statusMeta(user.status);
            const expanded = expandedUserId === user.id;
            const disabled = loading || actionPending || userRolesLoading;
            return <tr key={user.id} className="transition hover:bg-slate-50/80"><td className="px-5 py-3.5 text-sm font-semibold text-slate-900">{user.username}</td><td className="px-5 py-3.5 text-sm text-slate-600">{user.display_name}</td><td className="px-5 py-3.5 text-sm">{user.deleted_at ? <span className="inline-flex rounded-md bg-slate-100 px-2 py-1 text-xs font-medium text-slate-600">已软删</span> : <span className={`inline-flex rounded-md px-2 py-1 text-xs font-medium ${meta.className}`}>{meta.label}</span>}</td><td className="px-5 py-3.5 text-sm text-slate-500">{user.last_login_at ? new Date(user.last_login_at).toLocaleString("zh-CN") : "从未登录"}</td><td className="px-5 py-3.5 text-sm text-slate-500">{new Date(user.created_at).toLocaleDateString("zh-CN")}</td><td className="px-5 py-3.5"><div className="user-row-actions"><button type="button" disabled={disabled} className="inline-flex items-center gap-1 font-medium text-indigo-700 disabled:opacity-50" onClick={() => void selectUser(user)}><ShieldCheck size={13} />角色</button><button type="button" disabled={disabled} className="inline-flex items-center gap-1 rounded-md border border-slate-200 px-2 py-1 text-slate-600 hover:bg-slate-50 disabled:opacity-50" onClick={() => setExpandedUserId((current) => current === user.id ? null : user.id)}>{expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}更多</button>{expanded && <span className="user-row-action-menu">{!user.deleted_at && <><button type="button" disabled={disabled} className="inline-flex items-center gap-1 text-slate-700 disabled:opacity-50" title="编辑显示名" onClick={() => setDisplayNameTarget(user)}><Pencil size={13} />编辑</button><button type="button" disabled={disabled} className="inline-flex items-center gap-1 text-violet-700 disabled:opacity-50" title="重置密码" onClick={() => setPasswordTarget(user)}><KeyRound size={13} />重置密码</button></>}<button type="button" disabled={disabled} className="inline-flex items-center gap-1 text-slate-600 disabled:opacity-50" onClick={() => revokeSessions(user)}><RefreshCw size={13} />撤销会话</button>{user.deleted_at ? <button type="button" disabled={disabled} className="inline-flex items-center gap-1 text-emerald-700 disabled:opacity-50" onClick={() => restoreUser(user)}><Unlock size={13} />恢复</button> : <><button type="button" disabled={disabled} className="inline-flex items-center gap-1 text-amber-700 disabled:opacity-50" onClick={() => changeStatus(user)}>{user.status === "active" ? <><Ban size={13} />禁用</> : <><Check size={13} />启用</>}</button><button type="button" disabled={disabled} className="inline-flex items-center gap-1 text-rose-700 disabled:opacity-50" onClick={() => deleteUser(user)}><Trash2 size={13} />软删</button></>}</span>}</div></td></tr>;
          })}
          {!data?.users.length && <tr><td colSpan={6} className="px-5 py-14 text-center text-sm text-slate-500">暂无用户</td></tr>}
        </tbody></table>}
      </div></section>
      {data && <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-slate-500"><span>共 {data.total} 个用户 · 第 {currentPage}/{totalPages} 页 · 每页 {limit} 条</span><div className="flex flex-wrap items-center justify-end gap-2"><button type="button" aria-label="上一页" disabled={currentPage <= 1 || actionPending || loading} onClick={() => { const targetPage = currentPage - 1; setOffset((targetPage - 1) * limit); setPageInput(String(targetPage)); }} className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs hover:bg-slate-50 disabled:opacity-50"><ChevronLeft size={14} />上一页</button>{pageItems.map((item, index) => item === "ellipsis" ? <span key={`ellipsis-${index}`} className="px-1" aria-hidden="true">...</span> : <button key={item} type="button" aria-label={`第 ${item} 页`} aria-current={item === currentPage ? "page" : undefined} disabled={actionPending || loading || item === currentPage} onClick={() => { setOffset((item - 1) * limit); setPageInput(String(item)); }} className={`min-w-8 rounded-lg border px-2.5 py-1.5 text-xs disabled:opacity-100 ${item === currentPage ? "border-indigo-600 bg-indigo-600 text-white" : "border-slate-200 hover:bg-slate-50 disabled:cursor-default"}`}>{item}</button>)}<button type="button" aria-label="下一页" disabled={currentPage >= totalPages || actionPending || loading} onClick={() => { const targetPage = currentPage + 1; setOffset((targetPage - 1) * limit); setPageInput(String(targetPage)); }} className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs hover:bg-slate-50 disabled:opacity-50">下一页<ChevronRight size={14} /></button><form className="flex items-center gap-1.5" onSubmit={(event) => { event.preventDefault(); jumpToPage(); }}><label htmlFor="user-page-jump">跳转</label><input id="user-page-jump" className="w-16 rounded-lg border border-slate-200 px-2 py-1.5 text-center text-xs" type="number" min={1} max={totalPages} value={pageInput} disabled={actionPending || loading} onChange={(event) => setPageInput(event.target.value)} /><button type="submit" disabled={actionPending || loading} className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs hover:bg-slate-50 disabled:opacity-50">确定</button>{loading && <span className="text-xs text-slate-400">加载中...</span>}</form></div></div>}
      {selected && <section className="rounded-lg border border-blue-200 bg-blue-50 p-4"><div className="flex items-center justify-between"><h2 className="font-semibold text-gray-900">{selected.username} 的角色</h2><button type="button" className="text-sm text-gray-500" disabled={actionPending || userRolesLoading} onClick={() => setSelected(null)}>关闭</button></div>{userRolesLoading ? <div className="py-4 text-sm text-gray-500">加载角色中...</div> : <><div className="mt-3 flex flex-wrap gap-3">{activeRoles.map((role) => <label key={role.code} className="flex items-center gap-2 rounded bg-white px-3 py-2 text-sm"><input type="checkbox" disabled={actionPending} checked={selectedRoles.includes(role.code)} onChange={() => toggleRole(role.code)} />{role.name}（{role.code}）</label>)}</div><button type="button" disabled={actionPending} className="mt-3 rounded bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50" onClick={saveRoles}>{actionPending ? "保存中…" : "保存角色并撤销旧会话"}</button></>}</section>}
      <TextInputDialog key={displayNameTarget?.id ?? "display-name-dialog"} open={displayNameTarget !== null} title="编辑显示名" description={`修改 @${displayNameTarget?.username ?? "用户"} 在平台中显示的名称。`} label="显示名称" initialValue={displayNameTarget?.display_name ?? ""} placeholder="请输入显示名称" confirmLabel="保存修改" maxLength={128} onClose={() => setDisplayNameTarget(null)} onConfirm={updateDisplayName} />
      <PasswordResetDialog key={passwordTarget?.id ?? "password-dialog"} open={passwordTarget !== null} username={passwordTarget?.username ?? "用户"} onClose={() => setPasswordTarget(null)} onConfirm={resetPassword} />
    </div>
  );
}
