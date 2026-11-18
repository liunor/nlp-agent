import { AlertCircle, CheckCircle2, FileText, FileUp, Filter, History, RefreshCw, Send, Users, UserCheck, School, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useState, useRef, type ChangeEvent } from "react";

import {
  api,
  type DistributionTargetsResponse,
  type FileDistributionHistoryItem,
  type FileDistributionResponse,
  type StorageFile,
} from "@/platform/http/api";

function formatBytes(bytes: number) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1024 / 1024).toFixed(1) + " MB";
}

export function FileDistributionPage({
  workspaceId = "default",
  initialClassroomId,
}: {
  workspaceId?: string;
  initialClassroomId?: string;
}) {
  const [files, setFiles] = useState<StorageFile[]>([]);
  const [selectedFileId, setSelectedFileId] = useState<string>("");
  const [targetsMeta, setTargetsMeta] = useState<DistributionTargetsResponse | null>(null);
  const [targetType, setTargetType] = useState<"all_students" | "all_users" | "all_guests" | "all_admins" | "role" | "classroom" | "user">(
    initialClassroomId ? "classroom" : "all_students"
  );
  const [selectedRole, setSelectedRole] = useState("student");
  const [selectedClassroomId, setSelectedClassroomId] = useState(initialClassroomId || "");
  const [recipientIdentityId, setRecipientIdentityId] = useState("");
  const [verifiedRecipientName, setVerifiedRecipientName] = useState("");
  const [directDelivery, setDirectDelivery] = useState(true);

  const [loading, setLoading] = useState(true);
  const [distributing, setDistributing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [lastResult, setLastResult] = useState<FileDistributionResponse | null>(null);
  const [history, setHistory] = useState<FileDistributionHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  // Load teacher's files and distribution targets
  const loadData = useCallback(async () => {
    setLoading(true);
    setErrorMessage("");
    try {
      const [storageRes, targetsRes] = await Promise.all([
        api.listStorageFiles(workspaceId),
        api.getDistributionTargets(),
      ]);
      const availableFiles = storageRes.items.filter((item) => item.kind === "file");
      setFiles(availableFiles);
      if (availableFiles.length > 0 && !selectedFileId) {
        setSelectedFileId(availableFiles[0].id);
      }
      setTargetsMeta(targetsRes);
      if (!selectedClassroomId && targetsRes.classrooms.length > 0) {
        setSelectedClassroomId(targetsRes.classrooms[0].id);
      }
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "加载数据失败");
    } finally {
      setLoading(false);
    }
  }, [selectedClassroomId, selectedFileId, workspaceId]);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const res = await api.listDistributionHistory(30);
      setHistory(res.items);
    } catch {
      // Ignored
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadData();
      void loadHistory();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadData, loadHistory]);

  // Upload a new file from disk to teacher's storage directly
  const handleUpload = async (event: ChangeEvent<HTMLInputElement>) => {
    const uploadedFile = event.target.files?.[0];
    if (!uploadedFile) return;
    setUploading(true);
    setErrorMessage("");
    try {
      const created = await api.uploadStorageFile(uploadedFile, workspaceId);
      await loadData();
      setSelectedFileId(created.id);
      setStatusMessage(`文件《${uploadedFile.name}》已成功上传到您的云盘并选中。`);
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "上传文件失败");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  // Verify single recipient identity ID
  const handleVerifyRecipient = async () => {
    const trimmed = recipientIdentityId.trim();
    if (!trimmed) return;
    setErrorMessage("");
    try {
      const found = await api.lookupTransferRecipient(trimmed);
      setVerifiedRecipientName(found.display_name);
      setStatusMessage(`已找到接收用户：${found.display_name} (${found.identity_id})`);
    } catch (err) {
      setVerifiedRecipientName("");
      setErrorMessage(err instanceof Error ? err.message : "未找到该身份 ID 对应的用户");
    }
  };

  // Submit distribution
  const handleDistribute = async () => {
    if (!selectedFileId) {
      setErrorMessage("请先选择要分发的文件");
      return;
    }

    let targetValue: string | null = null;
    if (targetType === "role") targetValue = selectedRole;
    else if (targetType === "classroom") targetValue = selectedClassroomId;
    else if (targetType === "user") {
      targetValue = recipientIdentityId.trim();
      if (!targetValue) {
        setErrorMessage("请输入指定接收人的身份 ID 或用户名");
        return;
      }
    }

    setDistributing(true);
    setErrorMessage("");
    setStatusMessage("");
    setLastResult(null);

    try {
      const res = await api.distributeFile({
        source_file_id: selectedFileId,
        target_type: targetType,
        target_value: targetValue,
        direct_delivery: directDelivery,
        workspace_id: workspaceId,
      });

      setLastResult(res);
      setStatusMessage(
        `✅ 分发完成！已向 ${res.target_label} 发送文件《${res.file_name}》，成功 ${res.success_count} 人${
          res.failed_count > 0 ? `，失败 ${res.failed_count} 人` : ""
        }。`
      );
      void loadHistory();
      window.dispatchEvent(new Event("file-transfers:changed"));
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "分发文件失败");
    } finally {
      setDistributing(false);
    }
  };

  const selectedFile = files.find((f) => f.id === selectedFileId);

  return (
    <div className="teacher-stack file-distribution-container">
      {/* 头部介绍 */}
      <section className="teacher-welcome">
        <div>
          <h1>教师文件分发</h1>
          <p>
            向学生或用户下发学习资料、课件、代码实验包与练习。支持直接送达学生个人文件空间（免确认），也可发送接收请求。
          </p>
        </div>
        <Send size={56} className="text-primary opacity-80" />
      </section>

      {/* 消息提示 */}
      {errorMessage && (
        <div className="files-panel-error" role="alert" style={{ marginBottom: 12 }}>
          <AlertCircle size={16} />
          <span>{errorMessage}</span>
          <button type="button" onClick={() => setErrorMessage("")}>
            <X size={14} />
          </button>
        </div>
      )}
      {statusMessage && (
        <div
          style={{
            background: "rgba(16, 185, 129, 0.12)",
            border: "1px solid rgba(16, 185, 129, 0.3)",
            color: "#059669",
            padding: "10px 14px",
            borderRadius: 8,
            marginBottom: 12,
            fontSize: 14,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <CheckCircle2 size={16} />
          <span>{statusMessage}</span>
        </div>
      )}

      {/* 分发操作面板 */}
      <div className="teacher-panel" style={{ padding: 20 }}>
        <h2 style={{ fontSize: 18, marginBottom: 16, display: "flex", alignItems: "center", gap: 8 }}>
          <Sparkles size={20} />
          创建新分发
        </h2>

        {/* 步骤 1：选择文件 */}
        <div style={{ marginBottom: 24 }}>
          <label style={{ display: "block", fontWeight: 600, marginBottom: 8, fontSize: 14 }}>
            1. 选择要发送的文件
          </label>
          <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
            <select
              value={selectedFileId}
              onChange={(e) => setSelectedFileId(e.target.value)}
              disabled={loading || files.length === 0}
              style={{
                flex: "1 1 300px",
                padding: "8px 12px",
                borderRadius: 6,
                border: "1px solid var(--border-color, #ccc)",
                background: "var(--bg-input, #fff)",
                color: "inherit",
              }}
            >
              {files.length === 0 ? (
                <option value="">您的云盘中暂无文件</option>
              ) : (
                files.map((file) => (
                  <option key={file.id} value={file.id}>
                    {file.name} ({formatBytes(file.size_bytes)})
                  </option>
                ))
              )}
            </select>

            <button
              type="button"
              className="secondary"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              style={{ display: "flex", alignItems: "center", gap: 6, padding: "8px 14px" }}
            >
              <FileUp size={16} />
              {uploading ? "正在上传…" : "上传新文件并分发"}
            </button>
            <input
              ref={fileInputRef}
              type="file"
              style={{ display: "none" }}
              onChange={handleUpload}
            />
          </div>
          {selectedFile && (
            <small style={{ display: "block", marginTop: 6, color: "var(--text-muted, #666)" }}>
              已选择：{selectedFile.name} · {formatBytes(selectedFile.size_bytes)} · 类型：
              {selectedFile.mime_type || "未知"}
            </small>
          )}
        </div>

        {/* 步骤 2：选择目标对象 */}
        <div style={{ marginBottom: 24 }}>
          <label style={{ display: "block", fontWeight: 600, marginBottom: 8, fontSize: 14 }}>
            2. 选择分发目标范围
          </label>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
              gap: 12,
              marginBottom: 16,
            }}
          >
            {/* 所有学生 */}
            <div
              onClick={() => setTargetType("all_students")}
              style={{
                padding: "14px 16px",
                borderRadius: 8,
                border: `2px solid ${targetType === "all_students" ? "var(--color-primary, #3b82f6)" : "var(--border-color, #ddd)"}`,
                background: targetType === "all_students" ? "rgba(59, 130, 246, 0.08)" : "transparent",
                cursor: "pointer",
                transition: "all 0.2s",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600, marginBottom: 4 }}>
                <Users size={18} />
                向全体学生发送
              </div>
              <small style={{ color: "var(--text-muted, #666)" }}>
                系统内所有注册学生 ({targetsMeta?.summary.student_count ?? 0} 人)
              </small>
            </div>

            {/* 全体游客 */}
            <div
              onClick={() => setTargetType("all_guests")}
              style={{
                padding: "14px 16px",
                borderRadius: 8,
                border: `2px solid ${targetType === "all_guests" ? "var(--color-primary, #3b82f6)" : "var(--border-color, #ddd)"}`,
                background: targetType === "all_guests" ? "rgba(59, 130, 246, 0.08)" : "transparent",
                cursor: "pointer",
                transition: "all 0.2s",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600, marginBottom: 4 }}>
                <Users size={18} />
                向全体游客发送
              </div>
              <small style={{ color: "var(--text-muted, #666)" }}>
                系统内所有试用/游客账号 ({targetsMeta?.summary.guest_count ?? 0} 人)
              </small>
            </div>

            {/* 全体管理员 */}
            <div
              onClick={() => setTargetType("all_admins")}
              style={{
                padding: "14px 16px",
                borderRadius: 8,
                border: `2px solid ${targetType === "all_admins" ? "var(--color-primary, #3b82f6)" : "var(--border-color, #ddd)"}`,
                background: targetType === "all_admins" ? "rgba(59, 130, 246, 0.08)" : "transparent",
                cursor: "pointer",
                transition: "all 0.2s",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600, marginBottom: 4 }}>
                <UserCheck size={18} />
                向全体管理员发送
              </div>
              <small style={{ color: "var(--text-muted, #666)" }}>
                平台管理员与开发人员 ({targetsMeta?.summary.admin_count ?? 0} 人)
              </small>
            </div>

            {/* 所有用户 */}
            <div
              onClick={() => setTargetType("all_users")}
              style={{
                padding: "14px 16px",
                borderRadius: 8,
                border: `2px solid ${targetType === "all_users" ? "var(--color-primary, #3b82f6)" : "var(--border-color, #ddd)"}`,
                background: targetType === "all_users" ? "rgba(59, 130, 246, 0.08)" : "transparent",
                cursor: "pointer",
                transition: "all 0.2s",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600, marginBottom: 4 }}>
                <Users size={18} />
                向所有用户发送
              </div>
              <small style={{ color: "var(--text-muted, #666)" }}>
                平台全部活跃用户 ({targetsMeta?.summary.total_user_count ?? 0} 人)
              </small>
            </div>

            {/* 班级用户 */}
            <div
              onClick={() => setTargetType("classroom")}
              style={{
                padding: "14px 16px",
                borderRadius: 8,
                border: `2px solid ${targetType === "classroom" ? "var(--color-primary, #3b82f6)" : "var(--border-color, #ddd)"}`,
                background: targetType === "classroom" ? "rgba(59, 130, 246, 0.08)" : "transparent",
                cursor: "pointer",
                transition: "all 0.2s",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600, marginBottom: 4 }}>
                <School size={18} />
                指定某个班级
              </div>
              <small style={{ color: "var(--text-muted, #666)" }}>
                向所选班级的所有成员发送 ({targetsMeta?.classrooms.length ?? 0} 个班级可选)
              </small>
            </div>

            {/* 角色分类 */}
            <div
              onClick={() => setTargetType("role")}
              style={{
                padding: "14px 16px",
                borderRadius: 8,
                border: `2px solid ${targetType === "role" ? "var(--color-primary, #3b82f6)" : "var(--border-color, #ddd)"}`,
                background: targetType === "role" ? "rgba(59, 130, 246, 0.08)" : "transparent",
                cursor: "pointer",
                transition: "all 0.2s",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600, marginBottom: 4 }}>
                <Filter size={18} />
                按角色分类发送
              </div>
              <small style={{ color: "var(--text-muted, #666)" }}>发给学生、游客或教师群体</small>
            </div>

            {/* 指定单个用户 */}
            <div
              onClick={() => setTargetType("user")}
              style={{
                padding: "14px 16px",
                borderRadius: 8,
                border: `2px solid ${targetType === "user" ? "var(--color-primary, #3b82f6)" : "var(--border-color, #ddd)"}`,
                background: targetType === "user" ? "rgba(59, 130, 246, 0.08)" : "transparent",
                cursor: "pointer",
                transition: "all 0.2s",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600, marginBottom: 4 }}>
                <UserCheck size={18} />
                指定单个用户
              </div>
              <small style={{ color: "var(--text-muted, #666)" }}>精准发送给某一个学生或用户</small>
            </div>
          </div>

          {/* 目标二级选择项 */}
          {targetType === "classroom" && (
            <div style={{ padding: "12px 14px", background: "rgba(0,0,0,0.03)", borderRadius: 6 }}>
              <label style={{ display: "block", marginBottom: 6, fontSize: 13, fontWeight: 500 }}>
                选择班级：
              </label>
              <select
                value={selectedClassroomId}
                onChange={(e) => setSelectedClassroomId(e.target.value)}
                style={{
                  width: "100%",
                  maxWidth: 400,
                  padding: "8px 12px",
                  borderRadius: 6,
                  border: "1px solid var(--border-color, #ccc)",
                }}
              >
                {targetsMeta?.classrooms.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} ({c.student_count} 名成员)
                  </option>
                ))}
              </select>
            </div>
          )}

          {targetType === "role" && (
            <div style={{ padding: "12px 14px", background: "rgba(0,0,0,0.03)", borderRadius: 6 }}>
              <label style={{ display: "block", marginBottom: 6, fontSize: 13, fontWeight: 500 }}>
                选择用户类别：
              </label>
              <select
                value={selectedRole}
                onChange={(e) => setSelectedRole(e.target.value)}
                style={{
                  width: "100%",
                  maxWidth: 400,
                  padding: "8px 12px",
                  borderRadius: 6,
                  border: "1px solid var(--border-color, #ccc)",
                }}
              >
                {targetsMeta?.roles.map((r) => (
                  <option key={r.code} value={r.code}>
                    {r.name} - {r.description}
                  </option>
                ))}
              </select>
            </div>
          )}

          {targetType === "user" && (
            <div style={{ padding: "12px 14px", background: "rgba(0,0,0,0.03)", borderRadius: 6 }}>
              <label style={{ display: "block", marginBottom: 6, fontSize: 13, fontWeight: 500 }}>
                接收人身份 ID 或用户名：
              </label>
              <div style={{ display: "flex", gap: 10, maxWidth: 450 }}>
                <input
                  type="text"
                  placeholder="例如：NV2X9A... 或用户名"
                  value={recipientIdentityId}
                  onChange={(e) => {
                    setRecipientIdentityId(e.target.value);
                    setVerifiedRecipientName("");
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void handleVerifyRecipient();
                  }}
                  style={{
                    flex: 1,
                    padding: "8px 12px",
                    borderRadius: 6,
                    border: "1px solid var(--border-color, #ccc)",
                  }}
                />
                <button type="button" onClick={handleVerifyRecipient}>
                  验证用户
                </button>
              </div>
              {verifiedRecipientName && (
                <div style={{ marginTop: 6, color: "#10b981", fontSize: 13 }}>
                  ✓ 接收人：{verifiedRecipientName}
                </div>
              )}
            </div>
          )}
        </div>

        {/* 步骤 3：发送方式 */}
        <div style={{ marginBottom: 24 }}>
          <label style={{ display: "block", fontWeight: 600, marginBottom: 8, fontSize: 14 }}>
            3. 发送方式
          </label>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <label style={{ display: "flex", alignItems: "flex-start", gap: 8, cursor: "pointer" }}>
              <input
                type="radio"
                name="deliveryMode"
                checked={directDelivery}
                onChange={() => setDirectDelivery(true)}
                style={{ marginTop: 4 }}
              />
              <div>
                <strong>直接送达（推荐）</strong>
                <p style={{ margin: 0, fontSize: 13, color: "var(--text-muted, #666)" }}>
                  文件将直接写入学生的个人文件空间，并在消息通知中记录为“已接收”，学生无需点击同意即可直接查看、下载或运行代码。
                </p>
              </div>
            </label>
            <label style={{ display: "flex", alignItems: "flex-start", gap: 8, cursor: "pointer" }}>
              <input
                type="radio"
                name="deliveryMode"
                checked={!directDelivery}
                onChange={() => setDirectDelivery(false)}
                style={{ marginTop: 4 }}
              />
              <div>
                <strong>发送接收请求</strong>
                <p style={{ margin: 0, fontSize: 13, color: "var(--text-muted, #666)" }}>
                  向学生发送文件传输请求，学生在“文件消息”通知中点击“同意”后，文件才会存入其文件空间。
                </p>
              </div>
            </label>
          </div>
        </div>

        {/* 提交按钮 */}
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <button
            type="button"
            className="primary"
            disabled={distributing || !selectedFileId}
            onClick={handleDistribute}
            style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 20px", fontSize: 15 }}
          >
            <Send size={16} />
            {distributing ? "正在发送与分发中…" : "立即发送文件"}
          </button>
        </div>
      </div>

      {/* 分发执行报告卡片 */}
      {lastResult && (
        <section className="teacher-panel" style={{ padding: 20 }}>
          <header>
            <h2 style={{ fontSize: 17, display: "flex", alignItems: "center", gap: 8 }}>
              <CheckCircle2 size={18} className="text-success" />
              最近一次分发报告
            </h2>
          </header>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12, margin: "16px 0" }}>
            <article style={{ padding: 12, background: "rgba(0,0,0,0.03)", borderRadius: 6 }}>
              <small style={{ color: "var(--text-muted, #666)" }}>文件名称</small>
              <div style={{ fontWeight: 600 }}>{lastResult.file_name}</div>
            </article>
            <article style={{ padding: 12, background: "rgba(0,0,0,0.03)", borderRadius: 6 }}>
              <small style={{ color: "var(--text-muted, #666)" }}>分发范围</small>
              <div style={{ fontWeight: 600 }}>{lastResult.target_label}</div>
            </article>
            <article style={{ padding: 12, background: "rgba(0,0,0,0.03)", borderRadius: 6 }}>
              <small style={{ color: "var(--text-muted, #666)" }}>成功送达</small>
              <div style={{ fontWeight: 700, color: "#10b981", fontSize: 18 }}>
                {lastResult.success_count} / {lastResult.total_targets} 人
              </div>
            </article>
            <article style={{ padding: 12, background: "rgba(0,0,0,0.03)", borderRadius: 6 }}>
              <small style={{ color: "var(--text-muted, #666)" }}>发送方式</small>
              <div style={{ fontWeight: 600 }}>{lastResult.direct_delivery ? "直接送达入库" : "请求确认模式"}</div>
            </article>
          </div>

          {lastResult.failures && lastResult.failures.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <h4 style={{ color: "#ef4444", fontSize: 14, marginBottom: 8 }}>失败名单 ({lastResult.failures.length} 人)：</h4>
              <ul style={{ paddingLeft: 20, fontSize: 13, color: "var(--text-muted, #666)" }}>
                {lastResult.failures.map((f, i) => (
                  <li key={i}>
                    {f.display_name} ({f.identity_id})：{f.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      {/* 分发历史记录 */}
      <section className="teacher-panel" style={{ padding: 20 }}>
        <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <h2 style={{ fontSize: 18, display: "flex", alignItems: "center", gap: 8 }}>
              <History size={18} />
              文件分发历史
            </h2>
            <p style={{ margin: "4px 0 0 0", fontSize: 13, color: "var(--text-muted, #666)" }}>
              查看历史下发的文件批次、接收对象与成功送达状态。
            </p>
          </div>
          <button
            type="button"
            className="secondary"
            onClick={loadHistory}
            disabled={historyLoading}
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            <RefreshCw size={14} className={historyLoading ? "spin" : ""} />
            刷新
          </button>
        </header>

        {historyLoading && history.length === 0 ? (
          <p className="teacher-empty-inline">正在加载历史分发记录…</p>
        ) : history.length === 0 ? (
          <p className="teacher-empty-inline">暂无文件分发记录，上方即可创建并直接发送给学生。</p>
        ) : (
          <div style={{ overflowX: "auto", marginTop: 16 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border-color, #eee)", textAlign: "left" }}>
                  <th style={{ padding: "10px 12px" }}>文件名称</th>
                  <th style={{ padding: "10px 12px" }}>大小</th>
                  <th style={{ padding: "10px 12px" }}>发送目标</th>
                  <th style={{ padding: "10px 12px" }}>发送方式</th>
                  <th style={{ padding: "10px 12px" }}>送达情况</th>
                  <th style={{ padding: "10px 12px" }}>分发时间</th>
                </tr>
              </thead>
              <tbody>
                {history.map((item) => (
                  <tr key={item.id} style={{ borderBottom: "1px solid var(--border-color, #f0f0f0)" }}>
                    <td style={{ padding: "12px", display: "flex", alignItems: "center", gap: 8 }}>
                      <FileText size={16} />
                      <strong>{item.file_name}</strong>
                    </td>
                    <td style={{ padding: "12px", color: "var(--text-muted, #666)" }}>
                      {formatBytes(item.size_bytes)}
                    </td>
                    <td style={{ padding: "12px" }}>{item.target_label}</td>
                    <td style={{ padding: "12px" }}>
                      <span
                        style={{
                          fontSize: 12,
                          padding: "2px 8px",
                          borderRadius: 4,
                          background: item.direct_delivery ? "rgba(16, 185, 129, 0.1)" : "rgba(59, 130, 246, 0.1)",
                          color: item.direct_delivery ? "#059669" : "#2563eb",
                        }}
                      >
                        {item.direct_delivery ? "直接送达" : "需确认"}
                      </span>
                    </td>
                    <td style={{ padding: "12px" }}>
                      <span style={{ fontWeight: 600, color: item.failed_count === 0 ? "#10b981" : "#eab308" }}>
                        {item.success_count} / {item.total_count} 人
                      </span>
                    </td>
                    <td style={{ padding: "12px", color: "var(--text-muted, #666)", fontSize: 13 }}>
                      {new Date(item.created_at).toLocaleString("zh-CN", { hour12: false })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
