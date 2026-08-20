// 菜单管理页：后端 /api/v1/system/menus 已存在，但菜单配置 UI 与权限绑定将在
// 阶段3 一并完善，此处为占位。
export function MenuManagementPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">菜单管理</h1>
        <p className="text-sm text-gray-500">配置管理后台导航菜单与权限可见性</p>
      </div>
      <div className="rounded-lg border border-dashed border-gray-300 bg-white p-10 text-center text-sm text-gray-500">
        菜单配置 UI 与权限绑定将在阶段3 完善，本页暂为占位。
      </div>
    </div>
  );
}
