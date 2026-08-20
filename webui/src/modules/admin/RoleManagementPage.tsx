// 角色权限页：后端接口（角色目录、权限分配、assignable-role 规则）将在阶段3
// 「用户/角色/工作区」补齐，此处为占位，避免前端早于后端伪造权限（满足 review 7.3）。
export function RoleManagementPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">角色权限</h1>
        <p className="text-sm text-gray-500">管理全局角色与权限分配</p>
      </div>
      <div className="rounded-lg border border-dashed border-gray-300 bg-white p-10 text-center text-sm text-gray-500">
        后端接口（角色目录、权限分配、assignable-role 规则）将在阶段3「用户/角色/工作区」补齐，本页暂为占位。
      </div>
    </div>
  );
}
