import { School, Plus, RefreshCw, ClipboardList } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/platform/http/api";
import type { ClassroomSummary, JoinRequest } from "@/shared/types";

const statusLabel: Record<string, string> = {
  pending: "待审核",
  approved: "审核通过",
  rejected: "未通过",
};

export function ClassroomSettingsSection({ canSubmit }: { canSubmit: boolean }) {
  const refreshVersion = useRef(0);
  const [requestLoadError, setRequestLoadError] = useState("");
  const [memberships, setMemberships] = useState<ClassroomSummary[]>([]);
  const [membershipError, setMembershipError] = useState("");
  const [joinCode, setJoinCode] = useState("");
  const [studentNumber, setStudentNumber] = useState("");
  const [requests, setRequests] = useState<JoinRequest[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const loadRequests = useCallback(async () => {
    if (!canSubmit) return;
    const version = ++refreshVersion.current;
    setLoading(true);
    const [history, membership] = await Promise.allSettled([
      api.listMyJoinRequests(),
      typeof api.listMyClassrooms === "function" ? api.listMyClassrooms() : Promise.resolve({ items: [] }),
    ]);
    if (version !== refreshVersion.current) return;
    if (history.status === "fulfilled") {
      setRequests(history.value.items);
      setRequestLoadError("");
    } else {
      setRequestLoadError("申请记录读取失败，请刷新重试。");
    }
    if (membership.status === "fulfilled") {
      setMemberships(membership.value.items);
      setMembershipError("");
    } else {
      setMembershipError("当前班级读取失败，请刷新重试");
    }
    setLoading(false);
  }, [canSubmit]);

  useEffect(() => {
    queueMicrotask(() => void loadRequests());
  }, [loadRequests]);

  const submit = async () => {
    const normalizedCode = joinCode.trim().toUpperCase();
    if (!normalizedCode || submitting) return;
    setSubmitting(true);
    setError("");
    setSuccess("");
    try {
      await api.submitClassroomJoinRequest({
        join_code: normalizedCode,
        ...(studentNumber.trim() ? { student_number: studentNumber.trim() } : {}),
      });
      setJoinCode("");
      setStudentNumber("");
      setSuccess("申请已提交，请等待教师审核");
      await loadRequests();
    } catch (reason) {
      const errorCode = reason instanceof ApiError
        ? reason.code
        : typeof reason === "object" && reason !== null && "code" in reason
          ? String((reason as { code?: unknown }).code)
          : undefined;
      if (errorCode === "class_join_request_pending") {
        setError("已申请该班级，请等待审核");
      } else if (errorCode === "class_join_member_exists") {
        setError("你已经加入该班级，无需重复申请");
      } else if (errorCode === "class_join_code_not_found") {
        setError("班级码不存在或班级不可加入");
      } else {
        setError(reason instanceof Error ? reason.message : "提交申请失败");
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (!canSubmit) {
    return <div className="settings-note">当前账号没有提交入班申请的权限。</div>;
  }

  return (
    <div className="classroom-settings-section">
      <section className="student-classroom-memberships" aria-label="当前已加入的班级">
        <header className="student-classroom-heading"><div><School size={17} /><h3>已加入的班级</h3><span className="student-classroom-count">{memberships.length}</span></div></header>
        {membershipError ? <p className="settings-note" role="alert">{membershipError}</p> : memberships.length ? <div className="student-classroom-member-list" tabIndex={0} aria-label="已加入班级列表">{memberships.map((item) => <article key={item.id}><span className="student-classroom-icon"><School size={18} /></span><strong>{item.name}</strong><small>已加入</small></article>)}</div> : <p className="student-classroom-empty">{loading ? "正在加载…" : "当前尚未加入班级"}</p>}
      </section>
      <section className="classroom-join-form" aria-label="申请加入班级">
        <header className="student-classroom-heading"><div><Plus size={17} /><h3>申请加入班级</h3></div></header>
        <div className="classroom-fields-grid">
          <label className="settings-field">
            <span>班级码</span>
            <input
              value={joinCode}
              onChange={(event) => setJoinCode(event.target.value.toUpperCase())}
              placeholder="例如 ABCD2345"
              maxLength={12}
              autoComplete="off"
            />
          </label>
          <label className="settings-field">
            <span>学号 <small>选填</small></span>
            <input
              value={studentNumber}
              onChange={(event) => setStudentNumber(event.target.value)}
              placeholder="可不填写"
              maxLength={32}
            />
          </label>
        </div>
        <div className="classroom-form-footer">
          <span className="classroom-form-hint">填写教师提供的班级码，审核通过后即可加入。</span>
          <button className="settings-primary-button" type="button" disabled={!joinCode.trim() || submitting} onClick={() => void submit()}>
            {submitting ? "提交中…" : "申请入班"}
          </button>
        </div>
        {success && <p className="feedback-success" role="status">{success}</p>}
        {error && <p className="error-card" role="alert">{error}</p>}
      </section>

      <section className="classroom-request-list" aria-label="我的申请">
        <header className="student-classroom-heading">
          <div><ClipboardList size={17} /><h3>我的申请</h3></div>
          <button type="button" className="student-classroom-refresh" aria-label="刷新申请" disabled={loading} onClick={() => void loadRequests()}><RefreshCw size={14} className={loading ? "spin" : undefined} />刷新</button>
        </header>
        {loading ? (
          <p className="settings-note classroom-request-empty">正在加载…</p>
        ) : requestLoadError ? <p className="settings-note" role="alert">{requestLoadError}</p> : requests.length === 0 ? (
          <p className="settings-note classroom-request-empty">暂无入班申请。</p>
        ) : (
          <div className="classroom-request-items" role="region" aria-label="入班申请记录" tabIndex={0}>
            {requests.map((request) => (
              <article key={request.id} className="classroom-request-item">
                <div>
                  <b>{request.class_name || "班级"}</b>
                  <small>{new Date(request.requested_at).toLocaleString("zh-CN")}</small>
                </div>
                <span className={`classroom-request-status status-${request.status}`}>
                  {statusLabel[request.status] ?? request.status}
                </span>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
