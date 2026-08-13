import { useCallback, useEffect, useState } from "react";

import { api } from "@/platform/http/api";
import type { PermissionCatalogItem, RoleCatalogItem } from "@/shared/types";

export function RoleManagementPage() {
  const [roles, setRoles] = useState<RoleCatalogItem[]>([]);
  const [permissions, setPermissions] = useState<PermissionCatalogItem[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [selectedRole, setSelectedRole] = useState<RoleCatalogItem | null>(null);
  const [permMap, setPermMap] = useState<Record<string, string[]>>({});
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [newCode, setNewCode] = useState("");
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [creating, setCreating] = useState(false);

  const loadCatalog = useCallback(async () => {
    setLoading(true);
    try {
      const [r, p] = await Promise.all([api.listRoles(), api.listPermissions()]);
      setRoles(r.items);
      setPermissions(p.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const selectRole = useCallback(async (code: string) => {
    setSelected(code);
    setError("");
    setMessage("");
    try {
      const [role, permResp] = await Promise.all([
        api.listRoles().then((r) => r.items.find((x) => x.code === code) ?? null),
        api.getRolePermissions(code),
      ]);
      setSelectedRole(role);
      setPermMap(permResp.permissions);
      setChecked(new Set(Object.values(permResp.permissions).flat()));
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载角色权限失败");
    }
  }, []);

  useEffect(() => {
    void loadCatalog();
  }, [loadCatalog]);

  useEffect(() => {
    if (roles.length && !selected) void selectRole(roles[0].code);
  }, [roles, selected, selectRole]);

  const toggle = (code: string) =>
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });

  const scopeOf = useCallback(
    (code: string): string => {
      for (const [scope, codes] of Object.entries(permMap)) {
        if (codes.includes(code)) return scope;
      }
      return "-";
    },
    [permMap],
  );

  const save = async () => {
    if (!selected) return;
    setSaving(true);
    setMessage("");
    try {
      const newScopes: Record<string, string[]> = {};
      for (const [scope, codes] of Object.entries(permMap)) {
        const kept = codes.filter((c) => checked.has(c));
        if (kept.length) newScopes[scope] = kept;
      }
      const alreadyScoped = new Set(Object.values(permMap).flat());
      for (const c of checked) {
        if (!alreadyScoped.has(c)) (newScopes.own ??= []).push(c);
      }
      await api.replaceRolePermissions(selected, Array.from(checked), newScopes);
      setPermMap(newScopes);
      setMessage("已保存角色权限");
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const createRole = async () => {
    if (!/^[a-z][a-z0-9_]{1,62}$/.test(newCode)) {
      setError("角色 code 需为小写字母开头，仅含小写字母/数字/下划线，长度 2-63");
      return;
    }
    setCreating(true);
    setError("");
    try {
      await api.createRole({ code: newCode, name: newName || newCode, description: newDesc });
      setNewCode("");
      setNewName("");
      setNewDesc("");
      await loadCatalog();
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败");
    } finally {
      setCreating(false);
    }
  };

  if (loading && !roles.length) return <div className="py-12 text-center text-gray-500">加载中...</div>;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">角色权限管理</h1>
        <p className="text-sm text-gray-500">管理角色及其权限分配</p>
      </div>
      {error && <div className="rounded bg-red-50 p-4 text-sm text-red-700">{error}</div>}
      {message && <div className="rounded bg-green-50 p-3 text-sm text-green-700">{message}</div>}

      <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <h2 className="mb-3 text-sm font-medium text-gray-700">新建角色</h2>
        <div className="flex flex-wrap gap-2">
          <input value={newCode} onChange={(e) => setNewCode(e.target.value)} placeholder="角色 code（如 content_editor）" className="rounded-md border border-gray-300 px-3 py-2 text-sm" />
          <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="显示名称" className="rounded-md border border-gray-300 px-3 py-2 text-sm" />
          <input value={newDesc} onChange={(e) => setNewDesc(e.target.value)} placeholder="描述" className="rounded-md border border-gray-300 px-3 py-2 text-sm" />
          <button type="button" disabled={creating} onClick={() => void createRole()} className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">创建</button>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <div className="rounded-lg border border-gray-200 bg-white p-2 shadow-sm">
          {roles.map((role) => (
            <button key={role.code} type="button" onClick={() => void selectRole(role.code)}
              className={`mb-1 flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm ${selected === role.code ? "bg-blue-50 text-blue-700" : "text-gray-700 hover:bg-gray-100"}`}>
              <span>
                <strong>{role.name}</strong>
                <small className="block text-xs text-gray-400">{role.code}{role.is_builtin ? " · 内置" : ""}</small>
              </span>
              <span className={`rounded px-1.5 py-0.5 text-xs ${role.status === "active" ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-600"}`}>{role.status}</span>
            </button>
          ))}
        </div>

        <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          {selectedRole ? (
            <>
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-gray-900">{selectedRole.name}</h2>
                  <p className="text-xs text-gray-500">{selectedRole.code} · 已授予 {checked.size} 项权限</p>
                </div>
                <button type="button" disabled={saving} onClick={() => void save()} className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
                  {saving ? "保存中..." : "保存权限"}
                </button>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {permissions.map((perm) => (
                  <label key={perm.code} className="flex items-start gap-2 rounded-md border border-gray-100 px-3 py-2 text-sm hover:bg-gray-50">
                    <input type="checkbox" checked={checked.has(perm.code)} onChange={() => toggle(perm.code)} className="mt-0.5" />
                    <span>
                      <strong className="text-gray-900">{perm.code}</strong>
                      <small className="block text-xs text-gray-500">{perm.name} · {perm.description}</small>
                      <small className="text-xs text-blue-600">当前 scope: {scopeOf(perm.code)}</small>
                    </span>
                  </label>
                ))}
              </div>
            </>
          ) : (
            <p className="py-12 text-center text-sm text-gray-500">请选择左侧角色</p>
          )}
        </div>
      </div>
    </div>
  );
}
