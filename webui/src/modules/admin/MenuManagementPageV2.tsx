import { useCallback, useEffect, useState } from "react";
import { api } from "@/platform/http/api";
import type { RbacRole, SystemMenu } from "@/shared/types";

export interface MenuManagementPageProps {
  onShellRefresh?: () => Promise<void>;
  refreshToken?: number;
}

export function MenuManagementPageV2({ onShellRefresh, refreshToken = 0 }: MenuManagementPageProps) {
  const [menus, setMenus] = useState<SystemMenu[]>([]);
  const [roles, setRoles] = useState<RbacRole[]>([]);
  const [selectedRole, setSelectedRole] = useState("");
  const [selectedMenus, setSelectedMenus] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [loadError, setLoadError] = useState("");
  const [loading, setLoading] = useState(true);
  const [selectionLoading, setSelectionLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const selectedRoleRecord = roles.find((role) => role.code === selectedRole);
  const canEdit = Boolean(selectedRoleRecord && !selectedRoleRecord.is_builtin);

  const load = useCallback(async () => {
    setLoading(true); setLoadError("");
    try {
      const [menuResult, roleResult] = await Promise.all([api.listMenus(), api.listRoles()]);
      setMenus(menuResult.items); setRoles(roleResult.items);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { queueMicrotask(() => void load()); }, [load, refreshToken]);

  const selectRole = async (code: string) => {
    if (saving || selectionLoading) return;
    if (dirty && !confirm("当前角色有未保存的菜单修改，确定切换吗？")) return;
    setSelectedRole(code); setMessage(""); setDirty(false); setSelectedMenus([]);
    if (!code) return;
    setSelectionLoading(true);
    try {
      setSelectedMenus((await api.listRoleMenus(code)).menu_ids);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "加载角色菜单失败");
    } finally {
      setSelectionLoading(false);
    }
  };

  const toggleMenu = (menuId: string) => {
    if (!canEdit || saving || selectionLoading) return;
    setDirty(true);
    setSelectedMenus((current) => current.includes(menuId) ? current.filter((id) => id !== menuId) : [...current, menuId]);
  };

  const save = async () => {
    if (!selectedRole || !canEdit || saving || !dirty) return;
    setSaving(true); setMessage("");
    try {
      await api.replaceRoleMenus(selectedRole, selectedMenus);
      setDirty(false); setMessage("菜单绑定已保存");
      await onShellRefresh?.();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return <div className="space-y-6">
    <div><h1 className="text-2xl font-bold text-gray-900">菜单管理</h1><p className="text-sm text-gray-500">菜单可见性只影响开发者平台导航，接口权限仍由服务端 RBAC 决定。</p></div>
    {loadError && <div className="developer-error"><strong>菜单和角色加载失败</strong><p>{loadError}</p><button type="button" onClick={() => void load()} disabled={loading}>重试</button></div>}
    {message && <div className="rounded bg-blue-50 p-3 text-sm text-blue-800">{message}</div>}
    <div className="flex gap-3"><select className="rounded border px-3 py-2 text-sm" value={selectedRole} disabled={saving || loading || selectionLoading} onChange={(event) => void selectRole(event.target.value)}><option value="">选择角色</option>{roles.map((role) => <option key={role.code} value={role.code}>{role.name}（{role.code}）</option>)}</select><button type="button" disabled={!canEdit || saving || selectionLoading || !dirty} className="rounded bg-blue-600 px-3 py-2 text-sm text-white disabled:opacity-50" onClick={() => void save()}>{saving ? "保存中…" : "保存菜单绑定"}</button>{dirty && <span className="self-center text-xs text-amber-700">有未保存修改</span>}</div>
    {selectedRoleRecord?.is_builtin && <p className="text-sm text-gray-500">内置角色菜单只读；请创建自定义角色后再配置。</p>}
    <div className="rounded border border-gray-200 bg-white" aria-busy={loading || selectionLoading || saving}>
      {loading && !menus.length ? <div className="py-12 text-center text-sm text-gray-500">加载菜单和角色中...</div> : !loading && !menus.length ? <div className="py-12 text-center text-sm text-gray-500">暂无菜单</div> : <><div className="grid grid-cols-[auto_1fr_1fr_auto] gap-3 border-b bg-gray-50 px-4 py-3 text-xs font-semibold text-gray-500"><span>显示</span><span>名称</span><span>路由</span><span>权限</span></div>{selectionLoading ? <div className="py-12 text-center text-sm text-gray-500">加载角色菜单中...</div> : menus.map((menu) => <label key={menu.id} className="grid grid-cols-[auto_1fr_1fr_auto] items-center gap-3 border-b px-4 py-3 text-sm last:border-0"><input type="checkbox" checked={selectedMenus.includes(menu.id)} onChange={() => toggleMenu(menu.id)} disabled={!canEdit || saving} /><span>{menu.name}</span><span className="text-gray-500">{menu.route_path ?? "-"}</span><span className="text-xs text-gray-500">{menu.permission_id ?? "公开"}</span></label>)}</>}
    </div>
  </div>;
}
