// Agent 会话页：会话列表/撤销的跨用户越权防护（P1-3）将在阶段2「认证与会话」
// 收尾时补齐后端接口，前端展示将在阶段5 接入，此处为占位。
export function AgentSessionListPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Agent 会话</h1>
        <p className="text-sm text-gray-500">查看和管理 Agent 会话</p>
      </div>
      <div className="rounded-lg border border-dashed border-gray-300 bg-white p-10 text-center text-sm text-gray-500">
        会话撤销的跨用户越权防护将在阶段2「认证与会话」收尾时补齐后端接口，本页暂为占位。
      </div>
    </div>
  );
}
