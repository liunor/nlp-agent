import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ChevronLeft, LayoutGrid, FolderKanban, RefreshCw, GraduationCap } from "lucide-react";
import { useAuth } from "@/platform/auth/AuthContext";

export type AdminPage = "overview" | "workspaces" | "classrooms";

const NAV: Array<{ page: AdminPage; label: string; icon: typeof LayoutGrid }> = [
  { page: "overview", label: "概览", icon: LayoutGrid },
  { page: "workspaces", label: "工作区", icon: FolderKanban },
  { page: "classrooms", label: "班级管理", icon: GraduationCap },
];

function currentPageFromPath(): AdminPage {
  const seg = location.pathname.split("/")[2] as AdminPage | undefined;
  return NAV.some((n) => n.page === seg) ? seg! : "overview";
}

export function AdminLayout({ children }: { children: ReactNode }) {
  const page = currentPageFromPath();
  const navigate = useNavigate();
  const { user, roles, logout } = useAuth();
  const [refreshing, setRefreshing] = useState(false);

  const nav = (next: AdminPage) => {
    navigate(next === "overview" ? "/admin" : `/admin/${next}`);
  };

  const handleRefresh = () => {
    setRefreshing(true);
    setTimeout(() => setRefreshing(false), 500);
  };

  return (
    <div className="flex min-h-screen bg-gray-50">
      <aside className="flex w-60 flex-col border-r border-gray-200 bg-white">
        <div className="flex items-center gap-2 border-b border-gray-200 px-5 py-4">
          <LayoutGrid size={20} className="text-blue-600" />
          <span>
            <strong className="text-sm text-gray-900">系统管理</strong>
            <small className="block text-xs text-gray-500">NLP 平台管理面板</small>
          </span>
        </div>

        <nav className="flex-1 space-y-1 px-3 py-4">
          {NAV.map(({ page: itemPage, label, icon: Icon }) => (
            <button
              key={itemPage}
              type="button"
              onClick={() => nav(itemPage)}
              className={`flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition ${
                page === itemPage ? "bg-blue-50 text-blue-700" : "text-gray-600 hover:bg-gray-100 hover:text-gray-900"
              }`}
            >
              <Icon size={17} />
              {label}
            </button>
          ))}
        </nav>

        <div className="border-t border-gray-200 px-3 py-3 space-y-2">
          <Link
            to="/"
            className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-gray-600 hover:bg-gray-100 hover:text-gray-900"
          >
            <ChevronLeft size={16} />
            返回学习模式
          </Link>
          <button
            type="button"
            onClick={() => void logout()}
            className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm text-red-600 hover:bg-red-50"
          >
            退出登录
          </button>
        </div>
      </aside>

      <main className="flex-1">
        <header className="flex items-center justify-between border-b border-gray-200 bg-white px-6 py-3">
          <div className="flex items-center gap-2 text-sm text-gray-500">
            <span className="font-medium text-gray-900">{user?.user_id ?? "管理员"}</span>
            {roles.length > 0 && (
              <span className="rounded bg-blue-50 px-2 py-0.5 text-xs text-blue-700">{roles.join(", ")}</span>
            )}
          </div>
          <button
            type="button"
            onClick={handleRefresh}
            disabled={refreshing}
            className="flex items-center gap-1.5 rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-50"
          >
            <RefreshCw className={refreshing ? "animate-spin" : ""} size={14} />
            刷新
          </button>
        </header>

        <div className="p-6">{children}</div>
      </main>
    </div>
  );
}
