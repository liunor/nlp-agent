import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "@/platform/http/api";
import type { JoinRequest } from "@/shared/types";

const statusLabel: Record<string, string> = {
  pending: "待审核",
  approved: "已加入",
  rejected: "未通过",
};

export function ClassroomSettingsSection({ canSubmit }: { canSubmit: boolean }) {
  const [joinCode, setJoinCode] = useState("");
  const [studentNumber, setStudentNumber] = useState("");
  const [requests, setRequests] = useState<JoinRequest[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const loadRequests = useCallback(async () => {
    if (!canSubmit) return;
    setLoading(true);
    try {
      const response = await api.listMyJoinRequests();
      setRequests(response.items);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "暂时无法读取入班申请");
    } finally {
      setLoading(false);
    }
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
      <div className="settings-note">输入教师提供的班级码申请加入，不需要填写班级 UUID。</div>
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
      <button className="settings-primary-button" type="button" disabled={!joinCode.trim() || submitting} onClick={() => void submit()}>
        {submitting ? "提交中…" : "申请入班"}
      </button>
      {success && <p className="feedback-success" role="status">{success}</p>}
      {error && <p className="error-card" role="alert">{error}</p>}

      <div className="classroom-request-list">
        <strong>我的申请</strong>
        {loading ? <p className="settings-note">正在加载…</p> : requests.length === 0 ? <p className="settings-note">暂无入班申请。</p> : requests.map((request) => (
          <article key={request.id} className="classroom-request-item">
            <div><b>{request.class_name || "班级"}</b><span>{statusLabel[request.status] ?? request.status}</span></div>
            <small>{new Date(request.requested_at).toLocaleString("zh-CN")}</small>
          </article>
        ))}
      </div>
    </div>
  );
}
