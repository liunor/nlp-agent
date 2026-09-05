import { useCallback, useEffect, useRef, useState } from "react";
import { Ban, Check, ChevronLeft, ChevronRight, KeyRound, MoreHorizontal, Pencil, RefreshCw, Search, ShieldCheck, Trash2, Unlock, UserPlus, X } from "lucide-react";
import { api } from "@/platform/http/api";
import type { RbacRole, UserListResponse, UserProfile } from "@/shared/types";
import { PasswordResetDialog } from "@/shared/ui/PasswordResetDialog";
import { TextInputDialog } from "@/shared/ui/TextInputDialog";

type PageItem = number | "ellipsis";

const USER_PAGE_SIZE = 10;
const USER_CACHE_LIMIT = 24;

type UserListCache = Map<string, UserListResponse>;

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
  const limit = USER_PAGE_SIZE;
  const [pageInput, setPageInput] = useState("1");
  const [actionMenuUserId, setActionMenuUserId] = useState<string | null>(null);
  const [selected, setSelected] = useState<UserProfile | null>(null);
  const [displayNameTarget, setDisplayNameTarget] = useState<UserProfile | null>(null);
  const [passwordTarget, setPasswordTarget] = useState<UserProfile | null>(null);
  const [selectedRoles, setSelectedRoles] = useState<string[]>([]);
  const [userRolesLoading, setUserRolesLoading] = useState(false);
  const [form, setForm] = useState({ username: "", display_name: "", password: "" });
  const [createRoles, setCreateRoles] = useState<string[]>([]);
  const requestVersion = useRef(0);
  const userRolesRequestVersion = useRef(0);
  const userCache = useRef<UserListCache>(new Map());

  const cacheKey = `${offset}:${limit}:${statusFilter}:${search}:${includeDeleted}`;
  const clearUserCache = useCallback(() => userCache.current.clear(), []);

  const load = useCallback(async (force = false) => {
    const requestId = ++requestVersion.current;
    setLoading(true); setError("");
    if (!force) {
      const cached = userCache.current.get(cacheKey);
      if (cached) {
        setData(cached);
        setLoading(false);
        return;
      }
    }
    try {
      const result = await api.listUsers(offset, limit, statusFilter || undefined, search || undefined, includeDeleted);
      if (requestId !== requestVersion.current) return;
      userCache.current.set(cacheKey, result);
      if (userCache.current.size > USER_CACHE_LIMIT) {
        const oldestKey = userCache.current.keys().next().value;
        if (oldestKey) userCache.current.delete(oldestKey);
      }
      setData(result);
    } catch (err) {
      if (requestId === requestVersion.current) setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      if (requestId === requestVersion.current) setLoading(false);
    }
  }, [cacheKey, includeDeleted, limit, offset, search, statusFilter]);

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

  useEffect(() => {
    if (refreshToken > 0) clearUserCache();
    queueMicrotask(() => void load());
  }, [clearUserCache, load, refreshToken]);
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
    clearUserCache();
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
      clearUserCache();
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
      else {
        clearUserCache();
        setData((current) => current ? { ...current, users: current.users.filter((item) => item.id !== user.id), total: Math.max(0, current.total - 1) } : current);
      }
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

  const roleName = (code: string) => roles.find((role) => role.code === code)?.name ?? code;
  const submitSearch = () => {
    const next = searchInput.trim();
    setSearch((current) => current === next ? current : next);
    setOffset(0);
    setPageInput("1");
  };

  return (
    <div className="user-manage-page">
      <header className="user-page-header">
        <div>
          <p className="user-eyebrow">CONTROL PLANE / ACCESS</p>
          <div className="flex items-end justify-between gap-3">
            <div>
              <h1 className="text-2xl font-bold tracking-tight text-slate-900">用户管理</h1>
              <p className="mt-1 text-sm text-slate-500">账户、角色与访问状态统一在这里维护</p>
            </div>
            <span className="hidden text-xs text-slate-400 sm:inline">服务端分页 · 缓存最近访问页</span>
          </div>
        </div>
      </header>

      <section className="user-create-panel" aria-labelledby="user-create-title">
        <div className="user-create-heading">
          <div>
            <p className="user-eyebrow">NEW ACCOUNT</p>
            <h2 id="user-create-title" className="text-base font-semibold text-slate-900">创建用户</h2>
            <p className="mt-1 text-xs text-slate-500">创建后默认赋予游客角色，可按需追加平台角色。</p>
          </div>
          <span className="user-panel-hint">管理员操作</span>
        </div>
        <form className="user-create-form" onSubmit={(event) => { event.preventDefault(); void create(); }}>
          <label className="user-field-label" htmlFor="create-username">用户名<input id="create-username" aria-label="用户名" autoComplete="off" className="user-form-input" placeholder="输入登录用户名" value={form.username} disabled={actionPending} onChange={(e) => setForm({ ...form, username: e.target.value })} /></label>
          <label className="user-field-label" htmlFor="create-display-name">显示名称<input id="create-display-name" aria-label="显示名称" className="user-form-input" placeholder="输入用户显示名称" value={form.display_name} disabled={actionPending} onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></label>
          <label className="user-field-label" htmlFor="create-password">初始密码<input id="create-password" aria-label="初始密码" autoComplete="new-password" className="user-form-input" type="password" placeholder="至少 8 位" value={form.password} disabled={actionPending} onChange={(e) => setForm({ ...form, password: e.target.value })} /></label>
          <div className="user-create-role-field"><span className="user-field-label">角色</span><div className="user-role-options">{activeRoles.map((role) => <label key={role.code} className="user-role-option"><input type="checkbox" disabled={actionPending || rolesLoading} checked={createRoles.includes(role.code)} onChange={() => toggleCreateRole(role.code)} />{role.name}</label>)}{!activeRoles.length && <span className="text-xs text-slate-400">{rolesLoading ? "加载角色…" : "默认游客"}</span>}</div></div>
          <button type="submit" disabled={actionPending || form.username.trim().length < 3 || form.display_name.trim().length < 1 || form.password.length < 8} className="user-primary-button">{actionPending ? "处理中…" : <><UserPlus size={15} />创建用户</>}</button>
        </form>
      </section>

      {(error || actionError || actionMessage) && <div className="user-feedback-stack" aria-live="polite">{error && <div className="user-feedback user-feedback-error">{error}</div>}{actionError && <div className="user-feedback user-feedback-error">{actionError}</div>}{actionMessage && <div className="user-feedback user-feedback-success">{actionMessage}</div>}</div>}

      <section className="user-table-card" aria-label="用户列表">
        <div className="user-table-heading">
          <div><div className="flex items-center gap-2"><h2 className="text-sm font-semibold text-slate-900">用户列表</h2>{data && <span className="user-count-badge">{data.total}</span>}</div><p className="mt-1 text-xs text-slate-500">按需加载账户数据，角色和更多操作在行内完成</p></div>
          <button type="button" aria-label="刷新用户列表" title="刷新用户列表" onClick={() => void load(true)} disabled={loading || actionPending} className="user-refresh-button"><RefreshCw className={loading ? "animate-spin" : ""} size={14} />刷新</button>
        </div>
        <form className="user-list-toolbar" onSubmit={(event) => { event.preventDefault(); submitSearch(); }}>
          <label className="user-search-box" htmlFor="user-keyword"><Search size={16} aria-hidden="true" /><span className="sr-only">搜索</span><input id="user-keyword" aria-label="搜索用户名或显示名称" placeholder="搜索用户名或显示名称" value={searchInput} onChange={(e) => setSearchInput(e.target.value)} /></label>
          <button type="submit" className="user-toolbar-button user-toolbar-primary"><Search size={14} />搜索</button>
          <select aria-label="状态筛选" className="user-filter-select" value={statusFilter} disabled={actionPending} onChange={(e) => { setStatusFilter(e.target.value); setOffset(0); setPageInput("1"); }}><option value="">全部状态</option><option value="active">活跃</option><option value="disabled">已禁用</option><option value="locked">已锁定</option><option value="deleted">已软删</option></select>
          <label className="user-deleted-filter"><input type="checkbox" disabled={actionPending} checked={includeDeleted} onChange={(e) => { setIncludeDeleted(e.target.checked); setOffset(0); setPageInput("1"); }} />包含软删</label>
        </form>

        <div className="user-table-scroll">
          {loading && data && <div className="user-table-loading" role="status" aria-live="polite"><div><RefreshCw className="animate-spin text-indigo-500" size={14} />正在更新列表</div></div>}
          {loading && !data ? <table aria-busy={loading} className="user-data-table animate-pulse"><thead><tr>{["用户名", "显示名称", "角色", "状态", "最后登录", "创建时间", "操作"].map((item) => <th key={item}>{item}</th>)}</tr></thead><tbody>{Array.from({ length: 7 }, (_, index) => <tr key={index}><td colSpan={7}><div className="user-skeleton-row">{Array.from({ length: 7 }, (_, cell) => <span key={cell} className={cell === 6 ? "user-skeleton-long" : cell === 2 || cell === 3 ? "user-skeleton-short" : ""} />)}</div></td></tr>)}</tbody></table> : <table aria-busy={loading} className={`user-data-table ${loading ? "opacity-60" : ""}`}><thead><tr>{["用户名", "显示名称", "角色", "状态", "最后登录", "创建时间", "操作"].map((item) => <th key={item}>{item}</th>)}</tr></thead><tbody>
            {(data?.users ?? []).map((user) => {
              const meta = statusMeta(user.status);
              const userRoles = user.roles ?? [];
              const disabled = loading || actionPending || userRolesLoading;
              return <tr key={user.id}><td className="user-identity-cell"><span>{user.username}</span><small>{user.id}</small></td><td>{user.display_name}</td><td><div className="user-role-chip-list">{userRoles.length ? userRoles.map((code) => <span key={code} className="user-role-chip">{roleName(code)}</span>) : <span className="text-xs text-slate-400">游客</span>}</div></td><td>{user.deleted_at ? <span className="user-status-pill user-status-deleted">已软删</span> : <span className={`user-status-pill ${meta.className}`}>{meta.label}</span>}</td><td>{user.last_login_at ? new Date(user.last_login_at).toLocaleString("zh-CN") : "从未登录"}</td><td>{new Date(user.created_at).toLocaleDateString("zh-CN")}</td><td><div className="user-row-actions"><button type="button" aria-label={`${user.username} 角色`} title="管理角色" disabled={disabled} className="user-icon-action user-role-trigger" onClick={() => void selectUser(user)}><ShieldCheck size={15} /></button><div className="user-action-details"><button type="button" aria-label={`${user.username} 操作`} aria-expanded={actionMenuUserId === user.id} title="更多操作" className="user-icon-action" disabled={disabled} onClick={() => setActionMenuUserId((current) => current === user.id ? null : user.id)}><MoreHorizontal size={16} /></button>{actionMenuUserId === user.id && <div role="menu" aria-label={`${user.username} 操作菜单`} className="user-action-menu">{!user.deleted_at && <><button type="button" role="menuitem" disabled={disabled} onClick={() => { setActionMenuUserId(null); setDisplayNameTarget(user); }}><Pencil size={14} />编辑显示名</button><button type="button" role="menuitem" disabled={disabled} onClick={() => { setActionMenuUserId(null); setPasswordTarget(user); }}><KeyRound size={14} />重置密码</button></>}<button type="button" role="menuitem" disabled={disabled} onClick={() => { setActionMenuUserId(null); revokeSessions(user); }}><RefreshCw size={14} />撤销会话</button>{user.deleted_at ? <button type="button" role="menuitem" disabled={disabled} onClick={() => { setActionMenuUserId(null); restoreUser(user); }}><Unlock size={14} />恢复</button> : <><button type="button" role="menuitem" disabled={disabled} onClick={() => { setActionMenuUserId(null); changeStatus(user); }}>{user.status === "active" ? <><Ban size={14} />禁用</> : <><Check size={14} />启用</>}</button><button type="button" role="menuitem" disabled={disabled} onClick={() => { setActionMenuUserId(null); deleteUser(user); }}><Trash2 size={14} />软删</button></>}</div>}</div></div></td></tr>;
            })}
            {!data?.users.length && <tr><td colSpan={7} className="user-empty-state">暂无用户</td></tr>}
          </tbody></table>}
        </div>

        {data && <footer className="user-table-footer"><span>共 {data.total} 个用户 · 第 {currentPage}/{totalPages} 页 · 每页 {limit} 条</span><div className="user-pagination-controls"><button type="button" aria-label="上一页" disabled={currentPage <= 1 || actionPending || loading} onClick={() => { const targetPage = currentPage - 1; setOffset((targetPage - 1) * limit); setPageInput(String(targetPage)); }} className="user-page-button"><ChevronLeft size={14} />上一页</button>{pageItems.map((item, index) => item === "ellipsis" ? <span key={`ellipsis-${index}`} className="user-page-ellipsis" aria-hidden="true">...</span> : <button key={item} type="button" aria-label={`第 ${item} 页`} aria-current={item === currentPage ? "page" : undefined} disabled={actionPending || loading || item === currentPage} onClick={() => { setOffset((item - 1) * limit); setPageInput(String(item)); }} className={`user-page-number ${item === currentPage ? "is-current" : ""}`}>{item}</button>)}<button type="button" aria-label="下一页" disabled={currentPage >= totalPages || actionPending || loading} onClick={() => { const targetPage = currentPage + 1; setOffset((targetPage - 1) * limit); setPageInput(String(targetPage)); }} className="user-page-button">下一页<ChevronRight size={14} /></button><form className="user-jump-form" onSubmit={(event) => { event.preventDefault(); jumpToPage(); }}><label htmlFor="user-page-jump">跳转</label><input id="user-page-jump" type="number" min={1} max={totalPages} value={pageInput} disabled={actionPending || loading} onChange={(event) => setPageInput(event.target.value)} /><button type="submit" disabled={actionPending || loading}>确定</button></form></div></footer>}
      </section>

      {selected && <div className="user-role-dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelected(null); }}><section className="user-role-dialog" role="dialog" aria-modal="true" aria-labelledby="user-role-dialog-title" onMouseDown={(event) => event.stopPropagation()}><header><div><p className="user-eyebrow">ACCESS CONTROL</p><h2 id="user-role-dialog-title">{selected.username} 的角色</h2></div><button type="button" aria-label="关闭角色编辑" className="user-dialog-close" disabled={actionPending || userRolesLoading} onClick={() => setSelected(null)}><X size={18} /></button></header>{userRolesLoading ? <div className="user-dialog-loading"><RefreshCw className="animate-spin text-indigo-500" size={16} />加载角色中…</div> : <><p className="user-dialog-description">选择该用户可以使用的控制面能力，保存后会撤销其旧会话。</p><div className="user-dialog-role-list">{activeRoles.map((role) => <label key={role.code} className="user-dialog-role"><input type="checkbox" aria-label={role.name} disabled={actionPending} checked={selectedRoles.includes(role.code)} onChange={() => toggleRole(role.code)} /><span><strong>{role.name}</strong><small>{role.code} · {role.description || "暂无说明"}</small></span></label>)}</div><footer><button type="button" className="user-secondary-button" disabled={actionPending} onClick={() => setSelected(null)}>取消</button><button type="button" className="user-primary-button" disabled={actionPending} onClick={saveRoles}>{actionPending ? "保存中…" : "保存角色"}</button></footer></>}</section></div>}
      <TextInputDialog key={displayNameTarget?.id ?? "display-name-dialog"} open={displayNameTarget !== null} title="编辑显示名" description={`修改 @${displayNameTarget?.username ?? "用户"} 在平台中显示的名称。`} label="显示名称" initialValue={displayNameTarget?.display_name ?? ""} placeholder="请输入显示名称" confirmLabel="保存修改" maxLength={128} onClose={() => setDisplayNameTarget(null)} onConfirm={updateDisplayName} />
      <PasswordResetDialog key={passwordTarget?.id ?? "password-dialog"} open={passwordTarget !== null} username={passwordTarget?.username ?? "用户"} onClose={() => setPasswordTarget(null)} onConfirm={resetPassword} />
    </div>
  );
}
