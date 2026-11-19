import { School, Users, UserPlus, Copy, Plus } from "lucide-react";
import { PaginatedStudentTable } from "@/shared/ui/PaginatedStudentTable";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { ConfirmDialog } from "@/shared/ui/ConfirmDialog";
import { ClassroomWorkspaceSelect } from "./ClassroomWorkspaceSelect";
import { api } from "@/platform/http/api";
import type { ClassroomSummary, JoinRequest } from "@/shared/types";

// 班级管理页覆盖创建班级、班级码查看和加入申请审批，
// 复用 V3 既有 /api/v1/classrooms 与 join-request 端点，避免第二套班级系统。
interface ClassroomManagementPageProps {
  workspaceId?: string;
}
type StudentMember = {
  user_id: string;
  username: string;
  display_name: string;
  member_role: string;
  status: string;
  joined_at?: string;
};
const studentMembers = (items: StudentMember[]) => items.filter((item) => item.member_role === "student" && item.status === "active");

export function ClassroomManagementPage({ workspaceId }: ClassroomManagementPageProps = {}) {
  const [classrooms, setClassrooms] = useState<ClassroomSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [requests, setRequests] = useState<JoinRequest[]>([]);
  const [students, setStudents] = useState<StudentMember[]>([]);
  const [searchKeyword, setSearchKeyword] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [joinCode, setJoinCode] = useState("");
  const [copyMessage, setCopyMessage] = useState("");
  const [creating, setCreating] = useState(false);
  const [className, setClassName] = useState("");
  const [reviewing, setReviewing] = useState<string | null>(null);
  const reviewInFlight = useRef(false);
  const [removing, setRemoving] = useState(false);
  const removeInFlight = useRef(false);
  const [pendingRemoval, setPendingRemoval] = useState<StudentMember | null>(null);
  const [detailErrors, setDetailErrors] = useState({ requests: "", code: "", members: "" });
  const [failedDetails, setFailedDetails] = useState({ requests: false, members: false });
  const [detailsLoading, setDetailsLoading] = useState(false);
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

  const loadRequests = useCallback(async (classroomId: string, version = selectionVersion.current) => {
    try {
      const res = await api.listJoinRequests(classroomId);
      if (version !== selectionVersion.current || selectedIdRef.current !== classroomId) return;
      setRequests(res.items);
      setFailedDetails((current) => ({ ...current, requests: false }));
      setDetailErrors((current) => ({ ...current, requests: "" }));
    } catch (err) {
      if (version !== selectionVersion.current || selectedIdRef.current !== classroomId) return;
      setFailedDetails((current) => ({ ...current, requests: true }));
      setDetailErrors((current) => ({ ...current, requests: err instanceof Error ? err.message : "加载申请失败" }));
    }
  }, []);

  const handleSelect = (id: string) => {
    const version = ++selectionVersion.current;
    selectedIdRef.current = id;
    setSelectedId(id);
    setRequests([]);
    setStudents([]);
    setJoinCode("");
    setCopyMessage("");
    setSearchKeyword("");
    setActionError("");
    setActionMessage("");
    setPendingRemoval(null);
    setError("");
    setFailedDetails({ requests: false, members: false });
    setDetailErrors({ requests: "", code: "", members: "" });
    setDetailsLoading(true);
    void Promise.allSettled([api.listJoinRequests(id), api.getClassroomJoinCode(id), api.listClassroomMembers(id)]).then(([requestResult, codeResult, studentResult]) => {
      if (version !== selectionVersion.current || selectedIdRef.current !== id) return;
      if (requestResult.status === "fulfilled") setRequests(requestResult.value.items);
      if (codeResult.status === "fulfilled") setJoinCode(codeResult.value.join_code);
      if (studentResult.status === "fulfilled") setStudents(studentMembers(studentResult.value.items));
      const failureMessage = (result: PromiseSettledResult<unknown>) => result.status === "rejected" ? result.reason instanceof Error ? result.reason.message : "加载班级详情失败" : "";
      setFailedDetails({ requests: requestResult.status === "rejected", members: studentResult.status === "rejected" });
      setDetailErrors({ requests: failureMessage(requestResult), code: failureMessage(codeResult), members: failureMessage(studentResult) });
      setDetailsLoading(false);
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
      await loadClassrooms();
      handleSelect(created.id);
      setActionMessage(`班级已创建，班级码：${created.join_code}`);
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
    if (!classroomId || reviewInFlight.current || removeInFlight.current) return;
    reviewInFlight.current = true;
    setReviewing(requestId);
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
      if (approve) {
        await api.listClassroomMembers(classroomId).then(res => {
          if (version === selectionVersion.current && selectedIdRef.current === classroomId) {
            setStudents(studentMembers(res.items || []));
            setFailedDetails((current) => ({ ...current, members: false }));
            setDetailErrors((current) => ({ ...current, members: "" }));
          }
        }).catch((err) => {
          if (version === selectionVersion.current) {
            setFailedDetails((current) => ({ ...current, members: true }));
            setDetailErrors((current) => ({ ...current, members: err instanceof Error ? err.message : "刷新学生名单失败" }));
          }
        });
      }
      if (version === selectionVersion.current && selectedIdRef.current === classroomId) setActionMessage(approve ? "申请已通过" : "申请已拒绝");
    } catch (err) {
      if (version === selectionVersion.current) setActionError(err instanceof Error ? err.message : "操作失败");
    } finally {
      reviewInFlight.current = false;
      setReviewing(null);
    }
  };
  const removeStudent = async () => {
    const classroomId = selectedIdRef.current;
    if (!classroomId || !pendingRemoval || removeInFlight.current || reviewInFlight.current) return;
    const student = pendingRemoval;
    const version = selectionVersion.current;
    removeInFlight.current = true;
    setRemoving(true);
    setPendingRemoval(null);
    setActionError("");
    setActionMessage("");
    try {
      await api.removeClassroomMember(classroomId, student.user_id);
      if (version !== selectionVersion.current) return;
      setStudents((current) => current.filter((item) => item.user_id !== student.user_id));
      setActionMessage("学生已移出班级");
    } catch (err) {
      if (version === selectionVersion.current) setActionError(err instanceof Error ? err.message : "移除学生失败");
    } finally {
      removeInFlight.current = false;
      setRemoving(false);
    }
  };
  const filteredStudents = students.filter(
    (s) =>
      s.display_name?.toLowerCase().includes(searchKeyword.toLowerCase()) ||
      s.username?.toLowerCase().includes(searchKeyword.toLowerCase())
  );

  return <div className="teacher-stack classroom-management">
    <section className="teacher-page-summary classroom-hero"><div><h2>班级管理</h2><p>管理任教班级、审核入班申请与维护学生名单。</p></div><span className="classroom-total"><School size={16} />{classrooms.length} 个班级</span></section>
    {error && <p className="classroom-notice error" role="alert">{error}</p>}
    {Object.values(detailErrors).some(Boolean) && <p className="classroom-notice error" role="alert">{Object.values(detailErrors).filter(Boolean).join("；")}</p>}
    {actionError && <p className="classroom-notice error" role="alert">{actionError}</p>}
    {actionMessage && <p className="classroom-notice success" role="status">{actionMessage}</p>}
    <section className="teacher-panel classroom-create">
      <form onSubmit={(event) => void createClassroom(event)}>
        <label>班级名称<input aria-label="班级名称" value={className} maxLength={128} onChange={(event) => setClassName(event.target.value)} placeholder="输入新班级名称" /></label>
        <ClassroomWorkspaceSelect workspaceId={workspaceId} value={activeWorkspaceId} onChange={setSelectedWorkspaceId} />
        <button className="classroom-primary" type="submit" disabled={creating || !className.trim() || !activeWorkspaceId}><Plus size={15} />{creating ? "创建中…" : "创建班级"}</button>
      </form>
    </section>
    <div className="classroom-main-grid">
      <section className="teacher-panel classroom-list">
        <header><div><h2>我的班级</h2><p>选择班级查看申请和学生</p></div><School size={17} /></header>
        <nav aria-label="班级列表">{classrooms.map((item) => <button type="button" key={item.id} aria-pressed={selectedId === item.id} onClick={() => handleSelect(item.id)} className={selectedId === item.id ? "active" : ""}><School size={16} /><span>{item.name}</span></button>)}</nav>
        {!classrooms.length && <p className="teacher-empty-state">{loading ? "正在加载班级…" : "暂无班级，请先创建班级"}</p>}
      </section>
      <section className="teacher-panel classroom-requests">
        <header><div><h2>加入申请{selectedId && !detailsLoading && !failedDetails.requests && <span className="classroom-count">{requests.length}</span>}</h2><p>{classrooms.find((item) => item.id === selectedId)?.name ?? "选择左侧班级后处理申请"}</p></div><UserPlus size={17} /></header>
        {selectedId && <div className="classroom-code"><span>班级码 <strong>{joinCode || (detailsLoading ? "加载中…" : "暂不可用")}</strong></span><button type="button" disabled={!joinCode} onClick={() => void copyJoinCode()}><Copy size={14} />复制班级码</button>{copyMessage && <small role="status">{copyMessage}</small>}</div>}
        {!selectedId ? <p className="teacher-empty-state">选择一个班级查看申请</p> : detailsLoading ? <p className="teacher-empty-state">正在加载班级详情…</p> : failedDetails.requests ? <p className="teacher-empty-state" role="alert">申请读取失败，请重新选择班级重试。</p> : !requests.length ? <p className="teacher-empty-state">暂无待处理申请</p> : <div className="classroom-management-requests-list" role="region" aria-label="待审核申请列表" tabIndex={0}>{requests.map((request) => <article key={request.id}>
          <div><strong>{request.display_name || request.user_name || request.user_id}</strong><small>学号：{request.student_number || "未填写"}</small></div>
          {request.status === "pending" ? <div className="classroom-actions"><button type="button" className="classroom-primary" disabled={reviewing !== null || removing} onClick={() => void handleReview(request.id, true)}>通过</button><button type="button" disabled={reviewing !== null || removing} onClick={() => void handleReview(request.id, false)}>拒绝</button></div> : <span>{request.status === "approved" ? "已通过" : "已拒绝"}</span>}
        </article>)}</div>}
      </section>
    </div>
    <section className="teacher-panel teacher-question-panel teacher-question-activity-panel classroom-roster">
      <header><div><h2>班级学生名单 <span className="classroom-count">共 {students.length} 人</span></h2><p>{classrooms.find((item) => item.id === selectedId)?.name ?? "选择班级查看学生名单"}</p></div><div className="classroom-search"><input aria-label="搜索学生" placeholder="搜索姓名或账号..." value={searchKeyword} onChange={(event) => setSearchKeyword(event.target.value)} /><button type="button" aria-label="移除筛选" onClick={() => setSearchKeyword("")}>清除</button><Users size={17} /></div></header>
      {!selectedId ? <p className="teacher-empty-state">选择一个班级查看学生</p> : detailsLoading ? <p className="teacher-empty-state">正在加载学生名单…</p> : failedDetails.members ? <p className="teacher-empty-state" role="alert">学生名单读取失败，请重新选择班级重试。</p> : !filteredStudents.length ? <p className="teacher-empty-state">{searchKeyword ? "没有找到匹配的学生" : "该班级暂无学生"}</p> : <PaginatedStudentTable key={`${selectedId}:${searchKeyword}`} headers={["学生", "加入时间", "状态", "操作"]} rows={filteredStudents.map((student) => ({ id: student.user_id, cells: [
        <><strong>{student.display_name || "未设置"}</strong><small>@{student.username}</small></>,
        student.joined_at ? new Date(student.joined_at).toLocaleDateString("zh-CN") : "—",
        <span className="classroom-member-status">活跃</span>,
        <button className="classroom-remove" type="button" disabled={removing || reviewing !== null} onClick={() => setPendingRemoval(student)}>移除</button>,
      ] }))} />}
    </section>
    {pendingRemoval && <ConfirmDialog open title="移除学生" description={`确定要将 ${pendingRemoval.display_name || pendingRemoval.username} 移出班级吗？`} confirmLabel="确认移除" onClose={() => setPendingRemoval(null)} onConfirm={() => void removeStudent()} />}
  </div>;
}
