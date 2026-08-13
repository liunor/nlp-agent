import { useCallback, useEffect, useState } from "react";

import { api } from "@/platform/http/api";
import type { MenuCatalogItem } from "@/shared/types";

export function MenuManagementPage() {
  const [items, setItems] = useState<MenuCatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await api.listMenus();
      setItems([...resp.items].sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">菜单管理</h1>
        <p className="text-sm text-gray-500">系统菜单与可见性配置</p>
      </div>
      {error && <div className="rounded bg-red-50 p-4 text-sm text-red-700">{error}</div>}
      {loading ? (
        <div className="py-12 text-center text-gray-500">加载中...</div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                {["名称", "类型", "路由", "组件", "权限", "可见", "状态"].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 bg-white">
              {items.map((m) => (
                <tr key={m.id} className="text-sm">
                  <td className="px-4 py-2 font-medium text-gray-900">{m.name}</td>
                  <td className="px-4 py-2 text-gray-500">{m.type}</td>
                  <td className="px-4 py-2 text-gray-700">{m.route_path ?? "-"}</td>
                  <td className="px-4 py-2 text-gray-700">{m.component_key ?? "-"}</td>
                  <td className="px-4 py-2 text-gray-700">{m.permission_id ?? "-"}</td>
                  <td className="px-4 py-2">
                    <span className={`rounded px-2 py-0.5 text-xs ${m.visible ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-600"}`}>{m.visible ? "可见" : "隐藏"}</span>
                  </td>
                  <td className="px-4 py-2 text-gray-500">{m.status}</td>
                </tr>
              ))}
              {items.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-8 text-center text-sm text-gray-500">暂无菜单</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
