import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/platform/http/api";
import type { ClassroomSummary, JoinRequest } from "@/shared/types";

// 班级管理页覆盖创建班级、班级码查看和加入申请审批，
// 复用 V3 既有 /api/v1/classrooms 与 join-request 端点，避免第二套班级系统。
interface ClassroomManagementPageProps {
  workspaceId?: string;
}

type WorkspaceOption = { id: string; name: string };

export function ClassroomManagementPage({ workspaceId }: ClassroomManagementPageProps = {}) {
  const [classrooms, setClassrooms] = useState<ClassroomSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [requests, setRequests] = useState<JoinRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [joinCode, setJoinCode] = useState("");
  const [copyMessage, setCopyMessage] = useState("");
  const [creating, setCreating] = useState(false);
  const [className, setClassName] = useState("");
  const [workspaceOptions, setWorkspaceOptions] = useState<WorkspaceOption[]>([]);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState(workspaceId ?? "");
  const selectionVersion = useRef(0);
  const selectedIdRef = useRef<string | null>(null);

  useEffect(() => { selectedIdRef.current = selectedId; }, [selectedId]);
  const activeWorkspaceId = workspaceId ?? selectedWorkspaceId;

  const loadClassrooms = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.listClassrooms();
      setClassrooms(res.items);
      setWorkspaceOptions((current) => {
        const next = new Map(current.map((item) => [item.id, item]));
        for (const item of res.items) next.set(item.workspace_id, { id: item.workspace_id, name: item.workspace_id });
        return [...next.values()];
      });
      return res.items;
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载班级失败");
      return [];
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    queueMicrotask(() => void loadClassrooms());
  }, [loadClassrooms]);

  useEffect(() => {
    if (workspaceId || typeof api.listWorkspaces !== "function") return;
    void api.listWorkspaces().then((res) => {
      const options = res.workspaces.map((item) => ({ id: item.id, name: item.name }));
      setWorkspaceOptions((current) => {
        const next = new Map(current.map((item) => [item.id, item]));
        for (const item of options) next.set(item.id, item);
        return [...next.values()];
      });
      if (!selectedWorkspaceId && options[0]) setSelectedWorkspaceId(options[0].id);
    }).catch(() => undefined);
  }, [selectedWorkspaceId, workspaceId]);

  const loadRequests = useCallback(async (classroomId: string, version = selectionVersion.current) => {
    try {
      const res = await api.listJoinRequests(classroomId);
      if (version !== selectionVersion.current || selectedIdRef.current !== classroomId) return;
      setRequests(res.items);
    } catch (err) {
      if (version !== selectionVersion.current || selectedIdRef.current !== classroomId) return;
      setError(err instanceof Error ? err.message : "加载申请失败");
    }
  }, []);

  const handleSelect = (id: string) => {
    const version = ++selectionVersion.current;
    selectedIdRef.current = id;
    setSelectedId(id);
    setRequests([]);
    setJoinCode("");
    setCopyMessage("");
    setError("");
    void Promise.all([api.listJoinRequests(id), api.getClassroomJoinCode(id)]).then(([requestResult, codeResult]) => {
      if (version !== selectionVersion.current || selectedIdRef.current !== id) return;
      setRequests(requestResult.items);
      setJoinCode(codeResult.join_code);
    }).catch((err) => {
      if (version !== selectionVersion.current || selectedIdRef.current !== id) return;
      setError(err instanceof Error ? err.message : "加载班级详情失败");
    });
  };

  const createClassroom = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = className.trim();
    if (!name || !activeWorkspaceId || creating) return;
    setCreating(true);
    setActionError("");
    setActionMessage("");
    try {
      const created = await api.createClassroom({ workspace_id: activeWorkspaceId, name });
      setClassName("");
      setActionMessage(`班级已创建，班级码：${created.join_code}`);
      await loadClassrooms();
      handleSelect(created.id);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "创建班级失败");
    } finally {
      setCreating(false);
    }
  };

  const copyJoinCode = async () => {
    if (!joinCode) return;
    try {
      await navigator.clipboard.writeText(joinCode);
      setCopyMessage("班级码已复制");
    } catch {
      setCopyMessage("复制失败，请手动记录班级码");
    }
  };

  const handleReview = async (requestId: string, approve: boolean) => {
    const classroomId = selectedIdRef.current;
    if (!classroomId) return;
    const version = selectionVersion.current;
    setActionError("");
    setActionMessage("");
    try {
      if (approve) {
        await api.approveJoinRequest(classroomId, requestId);
      } else {
        await api.rejectJoinRequest(classroomId, requestId);
      }
      await loadRequests(classroomId, version);
      if (version === selectionVersion.current && selectedIdRef.current === classroomId) setActionMessage(approve ? "申请已通过" : "申请已拒绝");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "操作失败");
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">班级管理</h1>
        <p className="text-sm text-gray-500">创建班级、查看班级码并处理学生加入申请</p>
      </div>

      {error && <div className="rounded bg-red-50 p-4 text-sm text-red-700">{error}</div>}
      {actionError && <div className="rounded bg-red-50 p-3 text-sm text-red-700">{actionError}</div>}
      {actionMessage && <div className="rounded bg-green-50 p-3 text-sm text-green-700" role="status">{actionMessage}</div>}

      <form onSubmit={(event) => void createClassroom(event)} className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
        <h2 className="mb-3 text-sm font-semibold text-gray-700">创建班级</h2>
        <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <label className="text-sm text-gray-600">
            班级名称
            <input aria-label="班级名称" value={className} onChange={(event) => setClassName(event.target.value)} placeholder="例如：高一语文一班" className="mt-1 block w-full rounded border border-gray-300 px-3 py-2" />
          </label>
          <label className="text-sm text-gray-600">
            工作区
            <select aria-label="工作区" value={activeWorkspaceId} onChange={(event) => setSelectedWorkspaceId(event.target.value)} disabled={Boolean(workspaceId)} className="mt-1 block w-full rounded border border-gray-300 px-3 py-2 disabled:bg-gray-100">
              {!activeWorkspaceId && <option value="">选择工作区</option>}
              {workspaceOptions.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}
              {activeWorkspaceId && !workspaceOptions.some((option) => option.id === activeWorkspaceId) && <option value={activeWorkspaceId}>{activeWorkspaceId}</option>}
            </select>
          </label>
          <button type="submit" disabled={creating || !className.trim() || !activeWorkspaceId} className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">{creating ? "创建中…" : "创建班级"}</button>
        </div>
      </form>

      <div className="grid gap-6 lg:grid-cols-2">
        <div>
          {loading && classrooms.length === 0 ? (
            <div className="py-12 text-center text-gray-500">加载中...</div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              {classrooms.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => handleSelect(c.id)}
                  className={`cursor-pointer rounded-lg border p-5 text-left shadow-sm transition hover:border-blue-300 hover:shadow-md ${
                    selectedId === c.id ? "border-blue-300 bg-blue-50" : "border-gray-200 bg-white"
                  }`}
                >
                  <h3 className="font-semibold text-gray-900">{c.name}</h3>
                  <p className="mt-1 text-xs text-gray-500">状态：{c.status}</p>
                </button>
              ))}
              {classrooms.length === 0 && !loading && (
                <div className="col-span-full py-12 text-center text-gray-500">暂无班级</div>
              )}
            </div>
          )}
        </div>

        <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold text-gray-700">加入申请</h2>
          {selectedId && <div className="mb-4 rounded-md bg-blue-50 p-3 text-sm text-blue-900">
            <div className="flex items-center justify-between gap-3"><span>班级码：<strong className="tracking-widest">{joinCode || "加载中…"}</strong></span><button type="button" disabled={!joinCode} onClick={() => void copyJoinCode()} className="rounded border border-blue-200 bg-white px-2 py-1 text-xs font-medium hover:bg-blue-100">复制班级码</button></div>
            {copyMessage && <p className="mt-1 text-xs text-blue-700" role="status">{copyMessage}</p>}
          </div>}
          {!selectedId ? (
            <p className="py-8 text-center text-sm text-gray-500">选择一个班级查看申请</p>
          ) : requests.length === 0 ? (
            <p className="py-8 text-center text-sm text-gray-500">暂无待处理申请</p>
          ) : (
            <div className="max-h-96 space-y-3 overflow-y-auto">
              {requests.map((r) => (
                <div key={r.id} className="rounded-md border border-gray-200 p-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-gray-900">{r.display_name || r.user_name || r.user_id}</span>
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                        r.status === "pending"
                          ? "bg-yellow-100 text-yellow-800"
                          : r.status === "approved"
                            ? "bg-green-100 text-green-800"
                            : "bg-gray-100 text-gray-600"
                      }`}
                    >
                      {r.status}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-gray-500">学号：{r.student_number ?? "-"}</p>
                  {r.status === "pending" && (
                    <div className="mt-2 flex gap-2">
                      <button
                        type="button"
                        onClick={() => void handleReview(r.id, true)}
                        className="rounded bg-green-600 px-3 py-1 text-xs font-medium text-white hover:bg-green-700"
                      >
                        通过
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleReview(r.id, false)}
                        className="rounded bg-red-600 px-3 py-1 text-xs font-medium text-white hover:bg-red-700"
                      >
                        拒绝
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
