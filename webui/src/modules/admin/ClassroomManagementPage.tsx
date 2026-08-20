import { useCallback, useEffect, useState } from "react";
import { api } from "@/platform/http/api";
import type { ClassroomSummary, JoinRequest } from "@/shared/types";

// 班级管理页：仅覆盖"加入申请工作流"（提交/列表/审批/拒绝），
// 复用 V3 既有 /api/v1/classrooms 与 stage1 的 join-request 端点，
// 不重复实现类 CRUD / 成员管理（避免第二套班级系统，满足 review 4.1）。
export function ClassroomManagementPage() {
  const [classrooms, setClassrooms] = useState<ClassroomSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [requests, setRequests] = useState<JoinRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");

  const loadClassrooms = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.listClassrooms();
      setClassrooms(res.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载班级失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    queueMicrotask(() => void loadClassrooms());
  }, [loadClassrooms]);

  const loadRequests = useCallback(async (classroomId: string) => {
    setError("");
    try {
      const res = await api.listJoinRequests(classroomId);
      setRequests(res.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载申请失败");
    }
  }, []);

  const handleSelect = (id: string) => {
    setSelectedId(id);
    void loadRequests(id);
  };

  const handleReview = async (requestId: string, approve: boolean) => {
    if (!selectedId) return;
    setActionError("");
    try {
      if (approve) {
        await api.approveJoinRequest(selectedId, requestId);
      } else {
        await api.rejectJoinRequest(selectedId, requestId);
      }
      await loadRequests(selectedId);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "操作失败");
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">班级管理</h1>
        <p className="text-sm text-gray-500">处理学生加入班级的申请</p>
      </div>

      {error && <div className="rounded bg-red-50 p-4 text-sm text-red-700">{error}</div>}
      {actionError && <div className="rounded bg-red-50 p-3 text-sm text-red-700">{actionError}</div>}

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
