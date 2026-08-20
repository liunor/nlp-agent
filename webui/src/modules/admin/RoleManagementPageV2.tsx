import { useEffect, useState } from "react";
import { api } from "@/platform/http/api";
import type { RbacPermission, RbacRole } from "@/shared/types";

export function RoleManagementPageV2() {
  const [roles, setRoles] = useState<RbacRole[]>([]);
  const [permissions, setPermissions] = useState<RbacPermission[]>([]);
  const [selected, setSelected] = useState<RbacRole | null>(null);
  const [grants, setGrants] = useState<string[]>([]);
  const [scopes, setScopes] = useState<Record<string, string[]>>({});
  const [message, setMessage] = useState("");
  const [newRole, setNewRole] = useState({ code: "", name: "", description: "" });

  const load = async () => {
    const [roleResult, permissionResult] = await Promise.all([api.listRoles(), api.listPermissions()]);
    setRoles(roleResult.items); setPermissions(permissionResult.items);
  };
  useEffect(() => { queueMicrotask(() => void load().catch((error) => setMessage(error instanceof Error ? error.message : "加载失败"))); }, []);

  const select = async (role: RbacRole) => {
    setSelected(role); setMessage("");
    try {
      const result = await api.listRolePermissions(role.code);
      setGrants(Object.keys(result.permissions)); setScopes(result.permissions);
    } catch (error) { setMessage(error instanceof Error ? error.message : "加载角色权限失败"); }
  };
  const toggle = (code: string) => setGrants((current) => current.includes(code) ? current.filter((item) => item !== code) : [...current, code]);
  const save = async () => {
    if (!selected || selected.is_builtin) return;
    try { await api.replaceRolePermissions(selected.code, grants, scopes); setMessage("已保存，受影响用户的授权版本已递增"); await load(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "保存失败"); }
  };
  const create = async () => {
    try {
      await api.createRole(newRole);
      setNewRole({ code: "", name: "", description: "" });
      setMessage("自定义角色已创建，请选择它配置权限");
      await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "创建角色失败"); }
  };
  const toggleStatus = async (role: RbacRole) => {
    try {
      await api.updateRoleStatus(role.code, role.status === "active" ? "disabled" : "active");
      setMessage("角色状态已更新，受影响用户的授权版本已递增");
      await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "更新角色状态失败"); }
  };

  return <div className="space-y-6"><div><h1 className="text-2xl font-bold text-gray-900">角色与权限</h1><p className="text-sm text-gray-500">四个内置角色由迁移种子提供；内置角色只读，自定义角色可配置权限和作用域。</p></div>{message && <div className="rounded bg-blue-50 p-3 text-sm text-blue-800">{message}</div>}<section className="rounded border border-gray-200 bg-white p-4"><h2 className="mb-3 text-sm font-semibold">新建自定义角色</h2><div className="grid gap-3 md:grid-cols-[1fr_1fr_2fr_auto]"><input className="rounded border px-3 py-2 text-sm" placeholder="代码，例如 reviewer" value={newRole.code} onChange={(event) => setNewRole({ ...newRole, code: event.target.value })} /><input className="rounded border px-3 py-2 text-sm" placeholder="名称" value={newRole.name} onChange={(event) => setNewRole({ ...newRole, name: event.target.value })} /><input className="rounded border px-3 py-2 text-sm" placeholder="描述（可选）" value={newRole.description} onChange={(event) => setNewRole({ ...newRole, description: event.target.value })} /><button type="button" className="rounded bg-blue-600 px-3 py-2 text-sm text-white disabled:opacity-50" disabled={!newRole.code || !newRole.name} onClick={() => void create()}>创建角色</button></div></section><div className="grid gap-6 lg:grid-cols-[280px_1fr]"><div className="space-y-2">{roles.map((role) => <div key={role.code} className={`rounded border p-3 ${selected?.code === role.code ? "border-blue-400 bg-blue-50" : "bg-white"}`}><button type="button" onClick={() => void select(role)} className="w-full text-left"><div className="flex justify-between"><strong>{role.name}</strong><span className="text-xs text-gray-500">{role.code}</span></div><p className="mt-1 text-xs text-gray-500">{role.description || "暂无描述"}</p><span className="text-xs text-gray-400">{role.is_builtin ? "内置角色" : "自定义角色"} · {role.status}</span></button>{!role.is_builtin && <button type="button" className="mt-2 text-xs text-amber-700" onClick={() => void toggleStatus(role)}>{role.status === "active" ? "禁用角色" : "启用角色"}</button>}</div>)}</div><section className="rounded border border-gray-200 bg-white p-4">{!selected ? <p className="py-12 text-center text-sm text-gray-500">选择角色查看权限</p> : <><div className="flex items-center justify-between"><div><h2 className="font-semibold">{selected.name}</h2><p className="text-xs text-gray-500">{selected.is_builtin ? "内置角色只读" : "修改后立即使旧授权快照失效"}</p></div>{!selected.is_builtin && <button type="button" className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white" onClick={() => void save()}>保存权限</button>}</div><div className="mt-4 grid gap-2 md:grid-cols-2">{permissions.map((permission) => <label key={permission.code} className="flex items-start gap-2 rounded border p-3 text-sm"><input type="checkbox" checked={grants.includes(permission.code)} disabled={selected.is_builtin} onChange={() => toggle(permission.code)} /><span><strong>{permission.name}</strong><small className="block text-xs text-gray-500">{permission.code}</small></span></label>)}</div></>}</section></div></div>;
}
