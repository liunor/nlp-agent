import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { api } from "@/platform/http/api";
import { NotFoundPage } from "@/app/NotFoundPage";
import { AdminLayout } from "./AdminLayout";
import { WorkspaceListPage } from "./WorkspaceListPage";
import { ClassroomManagementPage } from "./ClassroomManagementPage";

function AdminOverview() {
  const [stats, setStats] = useState<{ users: number | null; workspaces: number | null; sessions: number | null }>({
    users: null,
    workspaces: null,
    sessions: null,
  });
  const [error, setError] = useState("");
  useEffect(() => {
    void (async () => {
      try {
        const [u, w, s] = await Promise.all([
          api.listUsers(0, 1),
          api.listWorkspaces(),
          api.listSessions(),
        ]);
        setStats({ users: u.total, workspaces: w.workspaces.length, sessions: s.items.length });
      } catch (e) {
        setError(e instanceof Error ? e.message : "加载统计失败");
      }
    })();
  }, []);
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">系统管理概览</h1>
        <p className="text-sm text-gray-500">NLP 学习平台管理面板</p>
      </div>
      {error && <div className="rounded bg-red-50 p-4 text-sm text-red-700">{error}</div>}
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h3 className="text-sm font-medium text-gray-500">用户管理</h3>
          <p className="mt-2 text-2xl font-bold text-gray-900">{stats.users ?? "-"}</p>
          <p className="mt-1 text-xs text-gray-400">管理系统用户账户</p>
        </div>
        <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h3 className="text-sm font-medium text-gray-500">工作区</h3>
          <p className="mt-2 text-2xl font-bold text-gray-900">{stats.workspaces ?? "-"}</p>
          <p className="mt-1 text-xs text-gray-400">管理工作区和成员</p>
        </div>
        <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h3 className="text-sm font-medium text-gray-500">Agent 会话</h3>
          <p className="mt-2 text-2xl font-bold text-gray-900">{stats.sessions ?? "-"}</p>
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
        <Route path="workspaces" element={<WorkspaceListPage />} />
        <Route path="classrooms" element={<ClassroomManagementPage />} />
        <Route path="users" element={<Navigate to="/developer/users" replace />} />
        <Route path="roles" element={<Navigate to="/developer/roles" replace />} />
        <Route path="menus" element={<Navigate to="/developer/menus" replace />} />
        <Route path="audit" element={<Navigate to="/developer/audit" replace />} />
        <Route path="sessions" element={<Navigate to="/developer/sessions" replace />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </AdminLayout>
  );
}

export default AdminRoutes;
