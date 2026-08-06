import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "@/platform/http/api";
import type { Workspace, WorkspaceMember } from "@/shared/types";

export function WorkspaceListPage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [selectedWorkspace, setSelectedWorkspace] = useState<Workspace | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await api.listWorkspaces();
      setWorkspaces(result.workspaces);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    queueMicrotask(() => void load());
  }, [load]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">工作区管理</h1>
          <p className="text-sm text-gray-500">管理工作区和成员</p>
        </div>
        <button
          type="button"
          onClick={() => setShowCreate(true)}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
        >
          + 新建工作区
        </button>
      </div>

      {error && <div className="rounded bg-red-50 p-4 text-sm text-red-700">{error}</div>}

      {loading && workspaces.length === 0 ? (
        <div className="py-12 text-center text-gray-500">加载中...</div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {workspaces.map((ws) => (
            <div
              key={ws.id}
              className="cursor-pointer rounded-lg border border-gray-200 bg-white p-5 shadow-sm transition hover:border-blue-300 hover:shadow-md"
              onClick={() => setSelectedWorkspace(ws)}
            >
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="font-semibold text-gray-900">{ws.name}</h3>
                  <p className="mt-1 text-xs text-gray-500">/{ws.slug}</p>
                </div>
                <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-semibold ${ws.status === "active" ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-600"}`}>
                  {ws.status}
                </span>
              </div>
              <p className="mt-3 text-xs text-gray-400">
                创建于 {new Date(ws.created_at).toLocaleDateString("zh-CN")}
              </p>
            </div>
          ))}
          {workspaces.length === 0 && !loading && (
            <div className="col-span-full py-12 text-center text-gray-500">暂无工作区</div>
          )}
        </div>
      )}

      {showCreate && <CreateWorkspaceDialog onClose={() => setShowCreate(false)} onCreated={() => { void load(); setShowCreate(false); }} />}
      {selectedWorkspace && <WorkspaceDetailPanel workspace={selectedWorkspace} onClose={() => setSelectedWorkspace(null)} />}
    </div>
  );
}

function CreateWorkspaceDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [type, setType] = useState("learning");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) { setError("名称为必填项"); return; }
    setSubmitting(true);
    setError("");
    try {
      await api.createWorkspace({ name, slug: slug || undefined, type });
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
        <h2 className="mb-4 text-lg font-semibold text-gray-900">新建工作区</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && <div className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</div>}
          <div>
            <label className="block text-sm font-medium text-gray-700">名称 *</label>
            <input type="text" required value={name} onChange={(e) => setName(e.target.value)}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500" disabled={submitting} />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">Slug（可选）</label>
            <input type="text" value={slug} onChange={(e) => setSlug(e.target.value)} placeholder="留空则自动生成"
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500" disabled={submitting} />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">类型</label>
            <select value={type} onChange={(e) => setType(e.target.value)}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500">
              <option value="learning">学习</option>
              <option value="teaching">教学</option>
              <option value="development">开发</option>
            </select>
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose} className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50" disabled={submitting}>取消</button>
            <button type="submit" className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50" disabled={submitting}>
              {submitting ? "创建中..." : "创建"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function WorkspaceDetailPanel({ workspace, onClose }: { workspace: Workspace; onClose: () => void }) {
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [loadingMembers, setLoadingMembers] = useState(true);
  const [addUserId, setAddUserId] = useState("");
  const [addMemberType, setAddMemberType] = useState("member");
  const [error, setError] = useState("");

  const loadMembers = useCallback(async () => {
    setLoadingMembers(true);
    try {
      const result = await api.listWorkspaceMembers(workspace.id);
      setMembers(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载成员失败");
    } finally {
      setLoadingMembers(false);
    }
  }, [workspace.id]);

  useEffect(() => { queueMicrotask(() => void loadMembers()); }, [loadMembers]);

  const handleAddMember = async (e: FormEvent) => {
    e.preventDefault();
    if (!addUserId.trim()) return;
    try {
      await api.addWorkspaceMember(workspace.id, addUserId, addMemberType);
      setAddUserId("");
      await loadMembers();
    } catch (err) {
      setError(err instanceof Error ? err.message : "添加失败");
    }
  };

  const handleRemoveMember = async (userId: string) => {
    if (!confirm("确认移除该成员？")) return;
    try {
      await api.removeWorkspaceMember(workspace.id, userId);
      await loadMembers();
    } catch (err) {
      setError(err instanceof Error ? err.message : "移除失败");
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-full max-w-lg rounded-lg bg-white p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">{workspace.name}</h2>
            <p className="text-sm text-gray-500">/{workspace.slug}</p>
          </div>
          <button type="button" onClick={onClose} className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600">✕</button>
        </div>

        {error && <div className="mb-3 rounded bg-red-50 p-3 text-sm text-red-700">{error}</div>}

        {/* Members */}
        <div className="mb-4">
          <h3 className="mb-2 text-sm font-semibold text-gray-700">成员列表</h3>
          {loadingMembers ? (
            <p className="py-4 text-center text-sm text-gray-500">加载中...</p>
          ) : members.length === 0 ? (
            <p className="py-4 text-center text-sm text-gray-500">暂无成员</p>
          ) : (
            <div className="max-h-48 space-y-1 overflow-y-auto">
              {members.map((m) => (
                <div key={m.user_id} className="flex items-center justify-between rounded px-3 py-2 hover:bg-gray-50">
                  <div>
                    <span className="text-sm font-medium text-gray-900">{m.user_id}</span>
                    <span className="ml-2 text-xs text-gray-500">{m.member_type}</span>
                  </div>
                  <button type="button" onClick={() => void handleRemoveMember(m.user_id)}
                    className="rounded px-2 py-1 text-xs text-red-600 hover:bg-red-50">移除</button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Add member */}
        <form onSubmit={handleAddMember} className="flex gap-2">
          <input type="text" placeholder="用户 ID" value={addUserId} onChange={(e) => setAddUserId(e.target.value)}
            className="flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500" />
          <select value={addMemberType} onChange={(e) => setAddMemberType(e.target.value)}
            className="rounded-md border border-gray-300 px-2 py-2 text-sm">
            <option value="member">成员</option>
            <option value="admin">管理员</option>
            <option value="viewer">观察者</option>
          </select>
          <button type="submit" className="rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700">添加</button>
        </form>
      </div>
    </div>
  );
}
