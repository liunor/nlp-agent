// 审计日志页：审计事件写入将在阶段3（用户/工作区生命周期）与阶段2（认证与会话）
// 落地，前端展示将在阶段4/5 接入，此处为占位。
export function AuditLogPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">审计日志</h1>
        <p className="text-sm text-gray-500">查看管理员操作、角色变化、成员变更与会话撤销记录</p>
      </div>
      <div className="rounded-lg border border-dashed border-gray-300 bg-white p-10 text-center text-sm text-gray-500">
        审计事件写入将在阶段3/阶段2 落地，前端展示将在后续阶段接入，本页暂为占位。
      </div>
    </div>
  );
}
