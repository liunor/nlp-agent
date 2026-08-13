import { useCallback, useEffect, useState } from "react";
import { api } from "@/platform/http/api";
import type {
  ClassroomItem,
  ClassroomMemberItem,
  UserProfile,
  Workspace,
} from "@/shared/types";

export function ClassroomManagementPage() {
  const [items, setItems] = useState<ClassroomItem[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // 新建班级
  const [showCreate, setShowCreate] = useState(false);
  const [selectedWsId, setSelectedWsId] = useState("");
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);

  // 成员管理子视图
  const [active, setActive] = useState<ClassroomItem | null>(null);
  const [members, setMembers] = useState<ClassroomMemberItem[]>([]);
  const [memberLoading, setMemberLoading] = useState(false);
  const [memberError, setMemberError] = useState("");
  const [showAdd, setShowAdd] = useState(false);
  const [addRole, setAddRole] = useState<"teacher" | "student">("teacher");
  const [keyword, setKeyword] = useState("");
  const [userResults, setUserResults] = useState<UserProfile[]>([]);
  const [searching, setSearching] = useState(false);
  const [adding, setAdding] = useState(false);

  // 重命名 / 删除
  const [renameTarget, setRenameTarget] = useState<ClassroomItem | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ClassroomItem | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [classResp, wsResp] = await Promise.all([
        api.listClassrooms(),
        api.listWorkspaces(),
      ]);
      setItems(classResp.items);
      setWorkspaces(wsResp.workspaces);
      setSelectedWsId((prev) => prev || wsResp.workspaces[0]?.id || "");
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadMembers = useCallback(async (classroom: ClassroomItem) => {
    setMemberLoading(true);
    setMemberError("");
    try {
      const resp = await api.listClassroomMembers(classroom.id);
      setMembers(resp.items);
    } catch (e) {
      setMemberError(e instanceof Error ? e.message : "加载成员失败");
    } finally {
      setMemberLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async () => {
    if (!selectedWsId) {
      setError("请先选择所属工作区");
      return;
    }
    if (!name.trim()) {
      setError("班级名称必填");
      return;
    }
    setCreating(true);
    setError("");
    try {
      await api.createClassroom({ workspace_id: selectedWsId, name: name.trim() });
      setSelectedWsId("");
      setName("");
      setShowCreate(false);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败");
    } finally {
      setCreating(false);
    }
  };

  const openMembers = (c: ClassroomItem) => {
    setActive(c);
    setShowAdd(false);
    setKeyword("");
    setUserResults([]);
    void loadMembers(c);
  };

  // 重命名
  const openRename = (c: ClassroomItem) => {
    setRenameTarget(c);
    setRenameValue(c.name);
  };
  const doRename = async () => {
    if (!renameTarget || !renameValue.trim()) return;
    setRenaming(true);
    setError("");
    try {
      const updated = await api.updateClassroom(renameTarget.id, { name: renameValue.trim() });
      setItems((prev) => prev.map((it) => (it.id === updated.id ? updated : it)));
      setActive((prev) => (prev && prev.id === updated.id ? { ...prev, name: updated.name } : prev));
      setRenameTarget(null);
      setRenameValue("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "重命名失败");
    } finally {
      setRenaming(false);
    }
  };

  // 删除
  const openDelete = (c: ClassroomItem) => setDeleteTarget(c);
  const doDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setError("");
    try {
      await api.deleteClassroom(deleteTarget.id);
      setItems((prev) => prev.filter((it) => it.id !== deleteTarget.id));
      if (active && active.id === deleteTarget.id) setActive(null);
      setDeleteTarget(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    } finally {
      setDeleting(false);
    }
  };

  const searchUsers = useCallback(async (kw: string) => {
    setSearching(true);
    try {
      const resp = await api.listUsers(0, 50, undefined, kw || undefined);
      setUserResults(resp.users);
    } catch {
      setUserResults([]);
    } finally {
      setSearching(false);
    }
  }, []);

  const addMember = async (user: UserProfile) => {
    if (!active) return;
    setAdding(true);
    setMemberError("");
    try {
      await api.replaceClassroomMember(active.id, user.id, {
        member_role: addRole,
        status: "active",
      });
      setShowAdd(false);
      setKeyword("");
      setUserResults([]);
      await loadMembers(active);
    } catch (e) {
      setMemberError(e instanceof Error ? e.message : "添加成员失败");
    } finally {
      setAdding(false);
    }
  };

  const toggleMember = async (m: ClassroomMemberItem) => {
    if (!active) return;
    setMemberError("");
    try {
      await api.replaceClassroomMember(active.id, m.user_id, {
        member_role: m.member_role,
        status: m.status === "active" ? "disabled" : "active",
      });
      await loadMembers(active);
    } catch (e) {
      setMemberError(e instanceof Error ? e.message : "操作失败");
    }
  };

  if (active) {
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <button
              type="button"
              onClick={() => setActive(null)}
              className="mb-1 text-sm text-blue-600 hover:underline"
            >
              ← 返回班级列表
            </button>
            <h1 className="text-2xl font-bold text-gray-900">{active.name} · 成员管理</h1>
            <p className="text-sm text-gray-500">工作区: {active.workspace_id}</p>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => openRename(active)}
              className="rounded-md border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              重命名
            </button>
            <button
              type="button"
              onClick={() => openDelete(active)}
              className="rounded-md border border-red-300 px-3 py-2 text-sm font-medium text-red-600 hover:bg-red-50"
            >
              删除班级
            </button>
            <button
              type="button"
              onClick={() => setShowAdd(true)}
              className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
            >
              + 添加成员
            </button>
          </div>
        </div>
        {memberError && (
          <div className="rounded bg-red-50 p-4 text-sm text-red-700">{memberError}</div>
        )}
        {memberLoading ? (
          <div className="py-12 text-center text-gray-500">加载中...</div>
        ) : (
          <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
                <tr>
                  <th className="px-4 py-3">用户</th>
                  <th className="px-4 py-3">角色</th>
                  <th className="px-4 py-3">状态</th>
                  <th className="px-4 py-3 text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {members.map((m) => (
                  <tr key={m.user_id}>
                    <td className="px-4 py-3">
                      <div className="font-medium text-gray-900">{m.display_name}</div>
                      <div className="text-xs text-gray-400">@{m.username}</div>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${m.member_role === "teacher" ? "bg-purple-100 text-purple-800" : "bg-blue-100 text-blue-800"}`}>
                        {m.member_role === "teacher" ? "老师" : "学生"}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${m.status === "active" ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-600"}`}>
                        {m.status === "active" ? "启用" : "停用"}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button
                        type="button"
                        onClick={() => void toggleMember(m)}
                        className="text-sm text-blue-600 hover:underline"
                      >
                        {m.status === "active" ? "停用" : "启用"}
                      </button>
                    </td>
                  </tr>
                ))}
                {members.length === 0 && (
                  <tr>
                    <td colSpan={4} className="px-4 py-10 text-center text-gray-500">
                      该班级暂无成员
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {showAdd && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
            <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
              <h2 className="mb-4 text-lg font-semibold text-gray-900">添加成员</h2>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700">角色</label>
                  <select
                    value={addRole}
                    onChange={(e) => setAddRole(e.target.value as "teacher" | "student")}
                    className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                  >
                    <option value="teacher">老师</option>
                    <option value="student">学生</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700">搜索用户（用户名 / 显示名）</label>
                  <input
                    value={keyword}
                    onChange={(e) => {
                      setKeyword(e.target.value);
                      void searchUsers(e.target.value);
                    }}
                    placeholder="输入关键字搜索"
                    className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                  />
                  <p className="mt-2 text-xs text-gray-400">
                    {searching ? "搜索中..." : `共 ${userResults.length} 条结果`}
                  </p>
                </div>
                <div className="max-h-56 space-y-2 overflow-y-auto">
                  {userResults.map((u) => (
                    <div
                      key={u.id}
                      className="flex items-center justify-between rounded-md border border-gray-200 px-3 py-2"
                    >
                      <div>
                        <div className="text-sm font-medium text-gray-900">{u.display_name}</div>
                        <div className="text-xs text-gray-400">@{u.username} · {u.status}</div>
                      </div>
                      <button
                        type="button"
                        disabled={adding}
                        onClick={() => void addMember(u)}
                        className="rounded-md bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                      >
                        添加
                      </button>
                    </div>
                  ))}
                  {!searching && userResults.length === 0 && (
                    <p className="py-4 text-center text-sm text-gray-400">输入关键字以搜索用户</p>
                  )}
                </div>
                <div className="flex justify-end gap-3 pt-2">
                  <button
                    type="button"
                    onClick={() => setShowAdd(false)}
                    className="rounded-md border border-gray-300 px-4 py-2 text-sm"
                  >
                    取消
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">班级管理</h1>
          <p className="text-sm text-gray-500">创建与管理教学班级，点击班级进入成员管理</p>
        </div>
        <button type="button" onClick={() => setShowCreate(true)} className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700">+ 新建班级</button>
      </div>
      {error && <div className="rounded bg-red-50 p-4 text-sm text-red-700">{error}</div>}
      {loading ? (
        <div className="py-12 text-center text-gray-500">加载中...</div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((c) => (
            <div
              key={c.id}
              className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm transition hover:border-blue-400 hover:shadow"
            >
              <button type="button" onClick={() => openMembers(c)} className="block w-full text-left">
                <div className="flex items-start justify-between">
                  <div>
                    <h3 className="font-semibold text-gray-900">{c.name}</h3>
                    <p className="mt-1 text-xs text-gray-500">/{c.id}</p>
                  </div>
                  <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${c.status === "active" ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-600"}`}>{c.status}</span>
                </div>
                <p className="mt-2 text-xs text-gray-400">工作区: {c.workspace_id}</p>
                <p className="mt-3 text-xs font-medium text-blue-600">管理成员 →</p>
              </button>
              <div className="mt-3 flex items-center justify-end gap-4 border-t border-gray-100 pt-3">
                <button
                  type="button"
                  onClick={() => openRename(c)}
                  className="text-sm text-blue-600 hover:underline"
                >
                  重命名
                </button>
                <button
                  type="button"
                  onClick={() => openDelete(c)}
                  className="text-sm text-red-600 hover:underline"
                >
                  删除
                </button>
              </div>
            </div>
          ))}
          {items.length === 0 && (
            <div className="col-span-full py-12 text-center text-gray-500">暂无班级（当前账户未关联任何班级）</div>
          )}
        </div>
      )}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
            <h2 className="mb-4 text-lg font-semibold text-gray-900">新建班级</h2>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700">所属工作区</label>
                {workspaces.length === 0 ? (
                  <p className="mt-1 text-sm text-red-600">当前账户未关联任何工作区，无法创建班级</p>
                ) : (
                  <select
                    value={selectedWsId}
                    onChange={(e) => setSelectedWsId(e.target.value)}
                    className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                  >
                    {workspaces.map((w) => (
                      <option key={w.id} value={w.id}>{w.name}（{w.slug}）</option>
                    ))}
                  </select>
                )}
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700">班级名称</label>
                <input value={name} onChange={(e) => setName(e.target.value)} className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm" />
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <button type="button" onClick={() => setShowCreate(false)} className="rounded-md border border-gray-300 px-4 py-2 text-sm">取消</button>
                <button type="button" disabled={creating || workspaces.length === 0} onClick={() => void create()} className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">{creating ? "创建中..." : "创建"}</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {renameTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
            <h2 className="mb-4 text-lg font-semibold text-gray-900">重命名班级</h2>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700">班级名称</label>
                <input
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                  autoFocus
                />
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setRenameTarget(null)}
                  className="rounded-md border border-gray-300 px-4 py-2 text-sm"
                >
                  取消
                </button>
                <button
                  type="button"
                  disabled={renaming || !renameValue.trim()}
                  onClick={() => void doRename()}
                  className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  {renaming ? "保存中..." : "保存"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
            <h2 className="mb-2 text-lg font-semibold text-gray-900">删除班级</h2>
            <p className="text-sm text-gray-600">
              确定删除班级「<span className="font-medium text-gray-900">{deleteTarget.name}</span>」吗？
              该操作将同时移除其全部成员，且不可恢复。
            </p>
            <div className="flex justify-end gap-3 pt-4">
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                className="rounded-md border border-gray-300 px-4 py-2 text-sm"
              >
                取消
              </button>
              <button
                type="button"
                disabled={deleting}
                onClick={() => void doDelete()}
                className="rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                {deleting ? "删除中..." : "确认删除"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
