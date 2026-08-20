import { useEffect, useState } from "react";
import { api } from "@/platform/http/api";
import type { RbacRole, SystemMenu } from "@/shared/types";

export function MenuManagementPageV2() {
  const [menus, setMenus] = useState<SystemMenu[]>([]);
  const [roles, setRoles] = useState<RbacRole[]>([]);
  const [selectedRole, setSelectedRole] = useState("");
  const [selectedMenus, setSelectedMenus] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const selectedRoleRecord = roles.find((role) => role.code === selectedRole);
  const canEdit = Boolean(selectedRoleRecord && !selectedRoleRecord.is_builtin);
  useEffect(() => { void Promise.all([api.listMenus(), api.listRoles()]).then(([menuResult, roleResult]) => { setMenus(menuResult.items); setRoles(roleResult.items); }).catch((error) => setMessage(error instanceof Error ? error.message : "加载失败")); }, []);
  const selectRole = async (code: string) => { setSelectedRole(code); setMessage(""); try { setSelectedMenus((await api.listRoleMenus(code)).menu_ids); } catch (error) { setMessage(error instanceof Error ? error.message : "加载角色菜单失败"); } };
  const save = async () => { if (!selectedRole) return; try { await api.replaceRoleMenus(selectedRole, selectedMenus); setMessage("菜单绑定已保存"); } catch (error) { setMessage(error instanceof Error ? error.message : "保存失败"); } };
  return <div className="space-y-6"><div><h1 className="text-2xl font-bold text-gray-900">菜单管理</h1><p className="text-sm text-gray-500">菜单可见性只影响开发者平台导航，接口权限仍由服务端 RBAC 决定。</p></div>{message && <div className="rounded bg-blue-50 p-3 text-sm text-blue-800">{message}</div>}<div className="flex gap-3"><select className="rounded border px-3 py-2 text-sm" value={selectedRole} onChange={(e) => void selectRole(e.target.value)}><option value="">选择角色</option>{roles.map((role) => <option key={role.code} value={role.code}>{role.name}（{role.code}）</option>)}</select><button type="button" disabled={!canEdit} className="rounded bg-blue-600 px-3 py-2 text-sm text-white disabled:opacity-50" onClick={() => void save()}>保存菜单绑定</button></div>{selectedRoleRecord?.is_builtin && <p className="text-sm text-gray-500">内置角色菜单只读；请创建自定义角色后再配置。</p>}<div className="rounded border border-gray-200 bg-white"><div className="grid grid-cols-[auto_1fr_1fr_auto] gap-3 border-b bg-gray-50 px-4 py-3 text-xs font-semibold text-gray-500"><span>显示</span><span>名称</span><span>路由</span><span>权限</span></div>{menus.map((menu) => <label key={menu.id} className="grid grid-cols-[auto_1fr_1fr_auto] items-center gap-3 border-b px-4 py-3 text-sm last:border-0"><input type="checkbox" checked={selectedMenus.includes(menu.id)} onChange={() => setSelectedMenus((current) => current.includes(menu.id) ? current.filter((id) => id !== menu.id) : [...current, menu.id])} disabled={!canEdit} /><span>{menu.name}</span><span className="text-gray-500">{menu.route_path ?? "-"}</span><span className="text-xs text-gray-500">{menu.permission_id ?? "公开"}</span></label>)}</div></div>;
}
