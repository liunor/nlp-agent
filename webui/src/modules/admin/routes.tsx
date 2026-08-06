import { Route, Routes } from "react-router-dom";
import { NotFoundPage } from "@/app/NotFoundPage";
import { AdminLayout } from "./AdminLayout";
import { UserListPage } from "./UserListPage";
import { WorkspaceListPage } from "./WorkspaceListPage";
import { AgentSessionListPage } from "./AgentSessionListPage";

function AdminOverview() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">系统管理概览</h1>
        <p className="text-sm text-gray-500">NLP 学习平台管理面板</p>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h3 className="text-sm font-medium text-gray-500">用户管理</h3>
          <p className="mt-2 text-2xl font-bold text-gray-900">-</p>
          <p className="mt-1 text-xs text-gray-400">管理系统用户账户</p>
        </div>
        <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h3 className="text-sm font-medium text-gray-500">工作区</h3>
          <p className="mt-2 text-2xl font-bold text-gray-900">-</p>
          <p className="mt-1 text-xs text-gray-400">管理工作区和成员</p>
        </div>
        <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h3 className="text-sm font-medium text-gray-500">Agent 会话</h3>
          <p className="mt-2 text-2xl font-bold text-gray-900">-</p>
          <p className="mt-1 text-xs text-gray-400">查看和管理 Agent 会话</p>
        </div>
      </div>
    </div>
  );
}

export function AdminRoutes() {
  return (
    <AdminLayout>
      <Routes>
        <Route index element={<AdminOverview />} />
        <Route path="users" element={<UserListPage />} />
        <Route path="workspaces" element={<WorkspaceListPage />} />
        <Route path="sessions" element={<AgentSessionListPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </AdminLayout>
  );
}

export default AdminRoutes;
