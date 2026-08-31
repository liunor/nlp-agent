import { useCallback, useEffect, useState } from "react";
import { api } from "@/platform/http/api";
import type { RbacPermission, RbacRole } from "@/shared/types";

export interface RoleManagementPageProps {
  onShellRefresh?: () => Promise<void>;
  refreshToken?: number;
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

  const load = useCallback(async () => {
    setLoading(true); setLoadError("");
    try {
      const [roleResult, permissionResult] = await Promise.all([api.listRoles(), api.listPermissions()]);
      setRoles(roleResult.items); setPermissions(permissionResult.items);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { queueMicrotask(() => void load()); }, [load, refreshToken]);

  const select = async (role: RbacRole) => {
    if (saving) return;
    if (dirty && !confirm("当前角色有未保存的权限修改，确定切换吗？")) return;
    setSelected(role); setMessage(""); setDirty(false); setDetailLoading(true);
    try {
      const result = await api.listRolePermissions(role.code);
      setGrants(Object.keys(result.permissions)); setScopes(result.permissions);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "加载角色权限失败");
    } finally {
      setDetailLoading(false);
    }
  };

  const toggle = (code: string) => {
    if (!selected || saving || detailLoading) return;
    const granted = grants.includes(code);
    setDirty(true);
    if (granted) {
      setGrants((current) => current.filter((item) => item !== code));
      setScopes((previous) => {
        const next = { ...previous };
        delete next[code];
        return next;
      });
      return;
    }
    setGrants((current) => [...current, code]);
    setScopes((previous) => ({ ...previous, [code]: previous[code]?.length ? previous[code] : [code === "learning:feedback:read" || code.startsWith("system:") ? "system" : "own"] }));
  };

  const updateScope = (code: string, scope: string) => {
    setDirty(true);
    setScopes((current) => ({ ...current, [code]: [scope] }));
  };

  const save = async () => {
    if (!selected || saving || !dirty) return;
    setSaving(true); setMessage("");
    try {
      await api.replaceRolePermissions(selected.code, grants, scopes);
      setDirty(false); setMessage("已保存，受影响用户的授权版本已递增");
      await load();
      await onShellRefresh?.();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <div><h1 className="text-2xl font-bold text-gray-900">角色与权限</h1><p className="text-sm text-gray-500">系统固定提供游客、学生、教师、开发者四个角色，可直接调整各角色的权限和作用域。</p></div>
      {loadError && <div className="developer-error"><strong>角色和权限加载失败</strong><p>{loadError}</p><button type="button" onClick={() => void load()} disabled={loading}>重试</button></div>}
      {message && <div className="rounded bg-blue-50 p-3 text-sm text-blue-800">{message}</div>}
      <div className="grid gap-6 lg:grid-cols-[280px_1fr]" aria-busy={loading || saving}>
        <div className="space-y-2">
          {loading && !roles.length && <div className="py-12 text-center text-sm text-gray-500">加载角色和权限中...</div>}
          {!loading && !roles.length && <div className="py-12 text-center text-sm text-gray-500">暂无角色</div>}
          {roles.map((role) => <div key={role.code} className={`rounded border p-3 ${selected?.code === role.code ? "border-blue-400 bg-blue-50" : "bg-white"}`}><button type="button" disabled={saving} onClick={() => void select(role)} className="w-full text-left disabled:opacity-50"><div className="flex justify-between"><strong>{role.name}</strong><span className="text-xs text-gray-500">{role.code}</span></div><p className="mt-1 text-xs text-gray-500">{role.description || "固定系统角色"}</p><span className="text-xs text-gray-400">固定角色 · {role.status === "active" ? "启用" : role.status}</span></button></div>)}
        </div>
        <section className="rounded border border-gray-200 bg-white p-4">
          {!selected ? <p className="py-12 text-center text-sm text-gray-500">选择角色查看权限</p> : (
            <>
              <div className="flex items-center justify-between">
                <div><h2 className="font-semibold">{selected.name}</h2><p className="text-xs text-gray-500">{dirty ? "有未保存修改" : "权限变更后立即使旧授权快照失效"}</p></div>
                <button type="button" className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50" disabled={saving || detailLoading || !dirty} onClick={() => void save()}>{saving ? "保存中…" : "保存权限"}</button>
              </div>
              {detailLoading ? <div className="py-12 text-center text-sm text-gray-500">加载角色权限中...</div> : <div className="mt-4 grid gap-2 md:grid-cols-2">{permissions.map((permission) => { const granted = grants.includes(permission.code); const scope = scopes[permission.code]?.[0] ?? (permission.code.startsWith("system:") ? "system" : "own"); const scopeOptions = permission.code === "learning:feedback:read" ? [["system", "系统"]] : [["own", "本人"], ["workspace", "工作区"], ["classroom", "班级"], ["public", "公开"], ["system", "系统"]]; return <label key={permission.code} className="flex items-start gap-2 rounded border p-3 text-sm"><input type="checkbox" checked={granted} disabled={saving} onChange={() => toggle(permission.code)} /><span className="min-w-0 flex-1"><strong>{permission.name}</strong><small className="block text-xs text-gray-500">{permission.description || permission.code}</small>{granted && <select aria-label={`${permission.name}作用域`} className="mt-2 rounded border px-2 py-1 text-xs" value={scope} disabled={saving} onChange={(event) => updateScope(permission.code, event.target.value)}>{scopeOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>}</span></label>; })}</div>}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
