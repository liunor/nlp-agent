import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  BookOpen,
  Bot,
  Check,
  Database,
  KeyRound,
  Layers3,
  LockKeyhole,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  Users,
} from "lucide-react";
import { api } from "@/platform/http/api";
import type { RbacPermission, RbacRole } from "@/shared/types";

export interface RoleManagementPageProps {
  onShellRefresh?: () => Promise<void>;
  refreshToken?: number;
}

type PermissionGroupKey = "identity" | "learning" | "classroom" | "agent" | "system" | "quota" | "other";

type PermissionGroup = {
  key: PermissionGroupKey;
  label: string;
  description: string;
  icon: typeof KeyRound;
};

const PERMISSION_GROUPS: PermissionGroup[] = [
  { key: "identity", label: "身份与资料", description: "个人资料和账号信息", icon: KeyRound },
  { key: "learning", label: "学习与反馈", description: "课程、练习、进度和学习反馈", icon: BookOpen },
  { key: "classroom", label: "班级管理", description: "班级和成员协作能力", icon: Users },
  { key: "agent", label: "Agent 会话", description: "会话、任务和检查点操作", icon: Bot },
  { key: "system", label: "平台管理", description: "模型、工具、审计和系统设置", icon: ShieldCheck },
  { key: "quota", label: "额度管理", description: "额度查看、分配和运营", icon: Database },
  { key: "other", label: "其他能力", description: "未归类的系统能力", icon: Layers3 },
];

const CLASSROOM_SCOPED_PERMISSIONS = new Set([
  "learning:content:manage",
  "learning:progress:read_classroom",
  "learning:feedback:create",
  "classroom:classroom:create",
  "classroom:member:manage",
]);

const WORKSPACE_SCOPED_PERMISSIONS = new Set([
  "learning:content:read_workspace",
  "agent:session:read",
  "agent:turn:submit",
  "agent:event:replay",
]);

const SCOPE_LABELS: Record<string, string> = {
  public: "公开内容",
  own: "本人数据",
  classroom: "负责班级",
  workspace: "所在工作区",
  system: "全平台",
};

function permissionGroup(code: string): PermissionGroupKey {
  const domain = code.split(":", 1)[0];
  if (domain === "identity" || domain === "learning" || domain === "classroom" || domain === "agent" || domain === "system" || domain === "quota") return domain;
  return "other";
}

function defaultScope(code: string): string {
  if (code === "learning:content:read_public") return "public";
  if (code.startsWith("system:") || code === "learning:feedback:read" || code === "learning:feedback:write") return "system";
  if (CLASSROOM_SCOPED_PERMISSIONS.has(code)) return "classroom";
  if (WORKSPACE_SCOPED_PERMISSIONS.has(code)) return "workspace";
  return "own";
}

function scopeText(scope: string): string {
  return SCOPE_LABELS[scope] ?? scope;
}

function roleTone(code: string): string {
  if (code === "developer") return "developer";
  if (code === "teacher") return "teacher";
  if (code === "student") return "student";
  return "guest";
}

export function RoleManagementPageV2({ onShellRefresh, refreshToken = 0 }: RoleManagementPageProps) {
  const [roles, setRoles] = useState<RbacRole[]>([]);
  const [permissions, setPermissions] = useState<RbacPermission[]>([]);
  const [selected, setSelected] = useState<RbacRole | null>(null);
  const [grants, setGrants] = useState<string[]>([]);
  const [scopes, setScopes] = useState<Record<string, string[]>>({});
  const [message, setMessage] = useState("");
  const [loadError, setLoadError] = useState("");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [query, setQuery] = useState("");
  const [activeGroup, setActiveGroup] = useState<PermissionGroupKey | "all">("all");
  const [grantedOnly, setGrantedOnly] = useState(false);
  const roleRequestVersion = useRef(0);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [roleResult, permissionResult] = await Promise.all([api.listRoles(), api.listPermissions()]);
      setRoles(roleResult.items);
      setPermissions(permissionResult.items);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "加载角色和权限失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const selectRole = useCallback(async (role: RbacRole, options: { skipConfirm?: boolean } = {}) => {
    if (saving) return;
    if (!options.skipConfirm && dirty && selected?.code !== role.code && !window.confirm("当前角色有未保存的权限修改，确定切换吗？")) return;
    const requestId = ++roleRequestVersion.current;
    setSelected(role);
    setMessage("");
    setDirty(false);
    setDetailLoading(true);
    setGrants([]);
    setScopes({});
    try {
      const result = await api.listRolePermissions(role.code);
      if (requestId !== roleRequestVersion.current) return;
      setGrants(Object.keys(result.permissions));
      setScopes(result.permissions);
    } catch (error) {
      if (requestId === roleRequestVersion.current) setMessage(error instanceof Error ? error.message : "加载角色权限失败");
    } finally {
      if (requestId === roleRequestVersion.current) setDetailLoading(false);
    }
  }, [dirty, saving, selected]);

  useEffect(() => { queueMicrotask(() => void load()); }, [load, refreshToken]);

  useEffect(() => {
    if (!selected && roles.length > 0) queueMicrotask(() => void selectRole(roles[0]));
  }, [roles, selected, selectRole]);

  useEffect(() => {
    if (refreshToken > 0 && selected) queueMicrotask(() => void selectRole(selected, { skipConfirm: true }));
  }, [refreshToken, selected, selectRole]);

  const selectedGrantSet = useMemo(() => new Set(grants), [grants]);
  const filteredPermissions = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return permissions.filter((permission) => {
      if (activeGroup !== "all" && permissionGroup(permission.code) !== activeGroup) return false;
      if (grantedOnly && !selectedGrantSet.has(permission.code)) return false;
      if (!normalizedQuery) return true;
      return [permission.name, permission.code, permission.description].some((value) => value.toLocaleLowerCase().includes(normalizedQuery));
    });
  }, [activeGroup, grantedOnly, permissions, query, selectedGrantSet]);

  const groupedPermissions = useMemo(() => PERMISSION_GROUPS.map((group) => ({
    ...group,
    items: filteredPermissions.filter((permission) => permissionGroup(permission.code) === group.key),
  })).filter((group) => group.items.length > 0), [filteredPermissions]);

  const togglePermission = (permission: RbacPermission) => {
    if (!selected || saving || detailLoading || permission.status !== "active") return;
    const granted = selectedGrantSet.has(permission.code);
    setDirty(true);
    if (granted) {
      setGrants((current) => current.filter((code) => code !== permission.code));
      setScopes((current) => {
        const next = { ...current };
        delete next[permission.code];
        return next;
      });
      return;
    }
    setGrants((current) => [...current, permission.code]);
    setScopes((current) => ({ ...current, [permission.code]: current[permission.code]?.length ? current[permission.code] : [defaultScope(permission.code)] }));
  };

  const updateScope = (code: string, scope: string) => {
    if (saving || detailLoading) return;
    setDirty(true);
    setScopes((current) => ({ ...current, [code]: [scope] }));
  };

  const save = async () => {
    if (!selected || saving || !dirty) return;
    setSaving(true);
    setMessage("");
    const payloadScopes = Object.fromEntries(
      grants.map((code) => [code, scopes[code]?.length ? scopes[code] : [defaultScope(code)]]),
    ) as Record<string, string[]>;
    try {
      await api.replaceRolePermissions(selected.code, grants, payloadScopes);
      setDirty(false);
      setMessage("权限已保存，受影响用户的授权版本已更新");
      try {
        await onShellRefresh?.();
      } catch {
        setMessage("权限已保存，但页面刷新失败，请手动刷新");
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存权限失败");
    } finally {
      setSaving(false);
    }
  };

  const grantedCount = grants.length;
  const activePermissionCount = permissions.filter((permission) => permission.status === "active").length;

  return (
    <div className="developer-role-page" aria-busy={loading || saving}>
      <header className="developer-role-page-header">
        <div>
          <h1>角色与权限</h1>
          <p>用角色决定谁能进入平台能力；作用域决定这些能力能看到哪些数据。</p>
        </div>
        <div className="developer-role-overview-stats"><span><strong>{roles.length}</strong> 个固定角色</span><span><strong>{activePermissionCount}</strong> 项可用权限</span></div>
      </header>

      {loadError && <div className="developer-role-feedback developer-role-feedback-error" role="alert"><ShieldCheck size={16} /><div><strong>角色与权限加载失败</strong><p>{loadError}</p></div><button type="button" onClick={() => void load()} disabled={loading}>重试</button></div>}
      {message && <div className="developer-role-feedback developer-role-feedback-info" role="status"><Check size={16} /><span>{message}</span></div>}

      <div className="developer-role-layout">
        <aside className="developer-role-directory" aria-label="角色列表">
          <div className="developer-role-directory-heading"><div><h2>角色</h2></div><span>{roles.length || "—"}</span></div>
          <p className="developer-role-directory-description">系统固定角色，不支持新增；可调整每个角色的能力和数据范围。</p>
          <div className="developer-role-list">
            {loading && !roles.length && Array.from({ length: 4 }, (_, index) => <div className="developer-role-skeleton" key={index}><span /><span /><span /></div>)}
            {!loading && !roles.length && <div className="developer-role-empty">暂无可配置角色</div>}
            {roles.map((role) => {
              const RoleIcon = role.code === "developer" ? ShieldCheck : role.code === "teacher" ? Users : role.code === "student" ? BookOpen : KeyRound;
              return <button type="button" key={role.code} className={`developer-role-item ${roleTone(role.code)} ${selected?.code === role.code ? "active" : ""}`} aria-pressed={selected?.code === role.code} onClick={() => void selectRole(role)} disabled={saving}>
                <span className="developer-role-item-icon"><RoleIcon size={17} /></span>
                <span className="developer-role-item-content"><strong>{role.name}</strong><small>{role.description || "固定系统角色"}</small><em>{role.code} · 固定角色</em></span>
                {selected?.code === role.code && <Check size={15} className="developer-role-item-check" />}
              </button>;
            })}
          </div>
          <div className="developer-role-directory-note"><LockKeyhole size={14} /><span>权限保存后，受影响用户的旧授权会话会自动失效。</span></div>
        </aside>

        <section className="developer-role-editor" aria-label="角色权限编辑">
          {!selected ? <div className="developer-role-empty-editor"><ShieldCheck size={28} /><strong>{loading ? "正在读取角色目录…" : "暂无角色可配置"}</strong><p>角色目录加载完成后，这里会显示权限工作台。</p></div> : <>
            <header className="developer-role-editor-header">
              <div className="developer-role-editor-title"><span className={`developer-role-avatar ${roleTone(selected.code)}`}><ShieldCheck size={20} /></span><div><div className="developer-role-title-line"><h2>{selected.name}</h2><span className="developer-role-code">{selected.code}</span>{selected.is_builtin && <span className="developer-role-fixed">固定角色</span>}</div><p>{selected.description || "系统角色权限配置"}</p></div></div>
              <button type="button" className="developer-role-save" disabled={saving || detailLoading || !dirty} onClick={() => void save()}><Save size={15} />{saving ? "保存中…" : "保存权限"}</button>
            </header>

            <div className="developer-role-editor-meta"><span><strong>{grantedCount}</strong> / {activePermissionCount} 项已授权</span><span className={dirty ? "is-dirty" : ""}>{dirty ? "有未保存修改" : "当前配置已同步"}</span><span><Activity size={13} />变更会写入授权审计</span></div>

            <div className="developer-permission-toolbar"><label className="developer-permission-search"><Search size={15} /><input aria-label="搜索权限" placeholder="搜索权限名称、用途或编码" value={query} onChange={(event) => setQuery(event.target.value)} /></label><label className="developer-permission-toggle"><input type="checkbox" checked={grantedOnly} onChange={(event) => setGrantedOnly(event.target.checked)} />仅看已授权</label></div>
            <nav className="developer-permission-groups" aria-label="权限分组"><button type="button" className={activeGroup === "all" ? "active" : ""} aria-pressed={activeGroup === "all"} onClick={() => setActiveGroup("all")}>全部<span>{permissions.length}</span></button>{PERMISSION_GROUPS.map((group) => { const GroupIcon = group.icon; const count = permissions.filter((permission) => permissionGroup(permission.code) === group.key).length; return count ? <button type="button" key={group.key} className={activeGroup === group.key ? "active" : ""} aria-pressed={activeGroup === group.key} onClick={() => setActiveGroup(group.key)}><GroupIcon size={13} />{group.label}<span>{count}</span></button> : null; })}</nav>

            <div className="developer-role-permission-scroll">
              {detailLoading ? <div className="developer-role-permission-loading"><RefreshCw className="spin" size={20} /><span>正在加载 {selected.name} 的权限配置…</span></div> : groupedPermissions.length === 0 ? <div className="developer-role-empty-editor"><Search size={24} /><strong>没有匹配的权限</strong><p>换一个关键词，或取消“仅看已授权”。</p></div> : groupedPermissions.map((group) => { const GroupIcon = group.icon; return <section className="developer-permission-group" key={group.key}><header><div><h3><GroupIcon size={16} />{group.label}</h3><p>{group.description}</p></div><span>{group.items.filter((permission) => selectedGrantSet.has(permission.code)).length} / {group.items.length} 已授权</span></header><div className="developer-permission-grid">{group.items.map((permission) => { const granted = selectedGrantSet.has(permission.code); const disabled = saving || detailLoading || permission.status !== "active"; const currentScope = scopes[permission.code]?.[0] ?? defaultScope(permission.code); const availableScopes = [defaultScope(permission.code)]; if (!availableScopes.includes(currentScope)) availableScopes.push(currentScope); return <article className={`developer-permission-card ${granted ? "is-granted" : ""} ${permission.status !== "active" ? "is-disabled" : ""}`} key={permission.code}><div className="developer-permission-card-main"><label className="developer-permission-check"><input type="checkbox" aria-label={`授权 ${permission.name}`} checked={granted} disabled={disabled} onChange={() => togglePermission(permission)} /><span className="developer-permission-checkmark"><Check size={12} /></span><span><strong>{permission.name}</strong><small>{permission.description || "暂无用途说明"}</small><code>{permission.code}</code></span></label>{permission.status !== "active" && <span className="developer-permission-status">已停用</span>}</div>{granted && <label className="developer-permission-scope"><span>数据范围</span><select aria-label={`${permission.name}作用域`} value={currentScope} disabled={saving || detailLoading} onChange={(event) => updateScope(permission.code, event.target.value)}>{availableScopes.map((scope) => <option key={scope} value={scope}>{scopeText(scope)}</option>)}</select>{(scopes[permission.code]?.length ?? 0) > 1 && <small>已配置多个范围，保存时保留</small>}</label>}</article>; })}</div></section>; })}
            </div>
          </>}
        </section>
      </div>
    </div>
  );
}
