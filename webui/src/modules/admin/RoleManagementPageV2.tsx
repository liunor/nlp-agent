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
  const [newRole, setNewRole] = useState({ code: "", name: "", description: "" });

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
    if (!selected || selected.is_builtin || saving || detailLoading) return;
    setDirty(true);
    setGrants((current) => current.includes(code) ? current.filter((item) => item !== code) : [...current, code]);
  };

  const save = async () => {
    if (!selected || selected.is_builtin || saving || !dirty) return;
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

  const create = async () => {
    if (saving) return;
    setSaving(true); setMessage("");
    try {
      await api.createRole({ ...newRole, code: newRole.code.trim(), name: newRole.name.trim() });
      setNewRole({ code: "", name: "", description: "" });
      setMessage("自定义角色已创建，请选择它配置权限");
      await load();
      await onShellRefresh?.();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "创建角色失败");
    } finally {
      setSaving(false);
    }
  };

  const toggleStatus = async (role: RbacRole) => {
    if (saving) return;
    setSaving(true); setMessage("");
    try {
      await api.updateRoleStatus(role.code, role.status === "active" ? "disabled" : "active");
      setMessage("角色状态已更新，受影响用户的授权版本已递增");
      await load();
      await onShellRefresh?.();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "更新角色状态失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <div><h1 className="text-2xl font-bold text-gray-900">角色与权限</h1><p className="text-sm text-gray-500">四个内置角色由迁移种子提供；内置角色只读，自定义角色可配置权限和作用域。</p></div>
      {loadError && <div className="developer-error"><strong>角色和权限加载失败</strong><p>{loadError}</p><button type="button" onClick={() => void load()} disabled={loading}>重试</button></div>}
      {message && <div className="rounded bg-blue-50 p-3 text-sm text-blue-800">{message}</div>}
      <section className="rounded border border-gray-200 bg-white p-4">
        <h2 className="mb-3 text-sm font-semibold">新建自定义角色</h2>
        <div className="grid gap-3 md:grid-cols-[1fr_1fr_2fr_auto]">
          <input className="rounded border px-3 py-2 text-sm" placeholder="代码，例如 reviewer" value={newRole.code} disabled={saving} onChange={(event) => setNewRole({ ...newRole, code: event.target.value })} />
          <input className="rounded border px-3 py-2 text-sm" placeholder="名称" value={newRole.name} disabled={saving} onChange={(event) => setNewRole({ ...newRole, name: event.target.value })} />
          <input className="rounded border px-3 py-2 text-sm" placeholder="描述（可选）" value={newRole.description} disabled={saving} onChange={(event) => setNewRole({ ...newRole, description: event.target.value })} />
          <button type="button" className="rounded bg-blue-600 px-3 py-2 text-sm text-white disabled:opacity-50" disabled={saving || !newRole.code.trim() || !newRole.name.trim()} onClick={() => void create()}>{saving ? "处理中…" : "创建角色"}</button>
        </div>
      </section>
      <div className="grid gap-6 lg:grid-cols-[280px_1fr]" aria-busy={loading || saving}>
        <div className="space-y-2">
          {loading && !roles.length && <div className="py-12 text-center text-sm text-gray-500">加载角色和权限中...</div>}
          {!loading && !roles.length && <div className="py-12 text-center text-sm text-gray-500">暂无角色</div>}
          {roles.map((role) => <div key={role.code} className={`rounded border p-3 ${selected?.code === role.code ? "border-blue-400 bg-blue-50" : "bg-white"}`}><button type="button" disabled={saving} onClick={() => void select(role)} className="w-full text-left disabled:opacity-50"><div className="flex justify-between"><strong>{role.name}</strong><span className="text-xs text-gray-500">{role.code}</span></div><p className="mt-1 text-xs text-gray-500">{role.description || "暂无描述"}</p><span className="text-xs text-gray-400">{role.is_builtin ? "内置角色" : "自定义角色"} · {role.status}</span></button>{!role.is_builtin && <button type="button" disabled={saving} className="mt-2 text-xs text-amber-700 disabled:opacity-50" onClick={() => void toggleStatus(role)}>{role.status === "active" ? "禁用角色" : "启用角色"}</button>}</div>)}
        </div>
        <section className="rounded border border-gray-200 bg-white p-4">
          {!selected ? <p className="py-12 text-center text-sm text-gray-500">选择角色查看权限</p> : (
            <>
              <div className="flex items-center justify-between">
                <div><h2 className="font-semibold">{selected.name}</h2><p className="text-xs text-gray-500">{selected.is_builtin ? "内置角色只读" : dirty ? "有未保存修改" : "修改后立即使旧授权快照失效"}</p></div>
                {!selected.is_builtin && <button type="button" className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50" disabled={saving || detailLoading || !dirty} onClick={() => void save()}>{saving ? "保存中…" : "保存权限"}</button>}
              </div>
              {detailLoading ? <div className="py-12 text-center text-sm text-gray-500">加载角色权限中...</div> : <div className="mt-4 grid gap-2 md:grid-cols-2">{permissions.map((permission) => <label key={permission.code} className="flex items-start gap-2 rounded border p-3 text-sm"><input type="checkbox" checked={grants.includes(permission.code)} disabled={saving || selected.is_builtin} onChange={() => toggle(permission.code)} /><span><strong>{permission.name}</strong><small className="block text-xs text-gray-500">{permission.code}</small></span></label>)}</div>}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
