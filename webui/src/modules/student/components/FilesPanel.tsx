import { LearningImportPanel } from "./LearningImportPanel";
import { formatBytes, type FilesPanelPreviewRequest } from "./document-import";
export type { FilesPanelPreviewRequest } from "./document-import";
import { ArrowLeft, CalendarClock, Download, FileText, FileUp, FolderOpen, FolderPlus, Mail, MessageSquarePlus, Pencil, RefreshCw, RotateCcw, Send, Trash2, UserRound, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { createPortal } from "react-dom";

import {
  api,
  storageFileDownloadUrl,
  type DistributionTargetsResponse,
  type FileDistributionRequest,
  type FileTransfer,
  type FileTransferRecipient,
  type StorageFile,
  type StorageUsage,
  type StorageUsageBucket,
} from "@/platform/http/api";
import { createUuid } from "@/shared/utils/uuid";
import { readFileTransferUnreadSummary } from "./FileTransferNotifier";
function StorageMeter({ label, bucket }: { label: string; bucket: StorageUsageBucket }) {
  const percentage = Math.round(Math.min(1, Math.max(0, bucket.used_ratio)) * 100);
  return <div className={["storage-meter", bucket.state].join(" ")}>
    <div className="storage-meter-label"><strong>{label}</strong><span>{formatBytes(bucket.used_bytes)} / {formatBytes(bucket.quota_bytes)}</span></div>
    <div className="storage-meter-track" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percentage}><span className="storage-meter-fill" style={{ width: `${percentage}%` }} /></div>
  </div>;
}

export function parseUtcTimestamp(value: string) {
  const normalized = value.trim();
  const hasTimezone = /(?:z|[+-]\d{2}:?\d{2})$/i.test(normalized);
  return new Date(hasTimezone ? normalized : `${normalized}Z`);
}

function formatModifiedAt(value: string | null | undefined) {
  if (!value) return "修改日期未知";
  const date = parseUtcTimestamp(value);
  if (Number.isNaN(date.getTime())) return "修改日期未知";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date).replace(/\u200e/g, "");
}

type StorageDocument = Pick<StorageFile, "id" | "name" | "size_bytes" | "mime_type">;

function StorageManager({
  workspaceId,
  onRefreshUsage,
  onOpenDocument,
  openMessagesRequest = 0,
  openFilesRequest = 0,
}: {
  workspaceId?: string;
  onRefreshUsage: () => void;
  onOpenDocument: (item: StorageDocument, fileWorkspaceId?: string) => void;
  openMessagesRequest?: number;
  openFilesRequest?: number;
}) {
  const [items, setItems] = useState<StorageFile[]>([]);
  const [folderPath, setFolderPath] = useState<StorageFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [newFolderName, setNewFolderName] = useState("");
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [trashMode, setTrashMode] = useState(false);
  const [sendFile, setSendFile] = useState<StorageFile | null>(null);
  const [recipientIdentityId, setRecipientIdentityId] = useState("");
  const [recipient, setRecipient] = useState<FileTransferRecipient | null>(null);
  const [sendBusy, setSendBusy] = useState(false);
  const [sendMessage, setSendMessage] = useState("");
  const [sendIdempotencyKey, setSendIdempotencyKey] = useState("");
  const [currentUserRoles, setCurrentUserRoles] = useState<string[]>([]);
  const isTeacher = useMemo(
    () => currentUserRoles.some((role) => ["teacher", "developer", "admin"].includes(role)),
    [currentUserRoles]
  );
  const [teacherSendMode, setTeacherSendMode] = useState<"single" | "group">("single");
  const [distributeTargetType, setDistributeTargetType] = useState<"all_students" | "all_guests" | "all_admins" | "all_users" | "role" | "classroom">("all_students");
  const [distributeRole, setDistributeRole] = useState("student");
  const [distributeClassroomId, setDistributeClassroomId] = useState("");
  const [directDelivery, setDirectDelivery] = useState(true);
  const [distributionTargets, setDistributionTargets] = useState<DistributionTargetsResponse | null>(null);
  const [distributionDone, setDistributionDone] = useState(false);
  const [distributionHasFailures, setDistributionHasFailures] = useState(false);
  const [messagesOpen, setMessagesOpen] = useState(false);
  const [messageBox, setMessageBox] = useState<"incoming" | "outgoing">("incoming");
  const [transfers, setTransfers] = useState<FileTransfer[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [unreadTransfers, setUnreadTransfers] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const transferRequestRef = useRef(0);
  const handledMessagesReqRef = useRef(0);
  const handledFilesReqRef = useRef(0);
  const currentFolder = folderPath[folderPath.length - 1] ?? null;

  const refresh = useCallback(() => {
    setLoading(true);
    setError("");
    const request = trashMode ? api.listStorageTrash(workspaceId) : api.listStorageFiles(workspaceId, currentFolder?.id);
    void request
      .then((response) => setItems(response.items))
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取文件列表"))
      .finally(() => setLoading(false));
  }, [currentFolder?.id, trashMode, workspaceId]);

  useEffect(() => {
    const handle = window.setTimeout(refresh, 0);
    return () => window.clearTimeout(handle);
  }, [refresh]);

  useEffect(() => {
    let active = true;
    if (typeof api.getCurrentUser === "function") {
      void api.getCurrentUser().then((u) => {
        if (active && u?.roles) setCurrentUserRoles(u.roles);
      }).catch(() => undefined);
    }
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const listener = () => { refresh(); onRefreshUsage(); };
    window.addEventListener("sandbox:files-changed", listener);
    return () => window.removeEventListener("sandbox:files-changed", listener);
  }, [refresh, onRefreshUsage]);

  const refreshTransferSummary = useCallback(() => {
    void readFileTransferUnreadSummary()
      .then((value) => {
        if (value) setUnreadTransfers(value.unread_count);
      })
      .catch(() => undefined);
  }, []);
  useEffect(() => { refreshTransferSummary(); }, [refreshTransferSummary]);
  useEffect(() => {
    window.addEventListener("file-transfers:changed", refreshTransferSummary);
    return () => window.removeEventListener("file-transfers:changed", refreshTransferSummary);
  }, [refreshTransferSummary]);

  const refreshTransfers = useCallback((box: "incoming" | "outgoing" = messageBox) => {
    const requestId = transferRequestRef.current + 1;
    transferRequestRef.current = requestId;
    setMessagesLoading(true);
    setTransfers([]);
    void api.listFileTransfers(box)
      .then((value) => {
        if (transferRequestRef.current !== requestId) return;
        setTransfers(value.items);
        if (box === "incoming" && typeof api.markFileTransfersSeen === "function") {
          void api.markFileTransfersSeen()
            .then((res) => {
              if (res?.updated > 0) {
                refreshTransferSummary();
                window.dispatchEvent(new Event("file-transfers:changed"));
              }
            })
            .catch(() => undefined);
        }
      })
      .catch((reason: unknown) => {
        if (transferRequestRef.current === requestId) setError(reason instanceof Error ? reason.message : "无法读取文件消息");
      })
      .finally(() => {
        if (transferRequestRef.current === requestId) setMessagesLoading(false);
      });
  }, [messageBox, refreshTransferSummary]);

  const openMessages = useCallback(() => {
    setMessagesOpen(true);
    setMessageBox("incoming");
    setTransfers([]);
    refreshTransfers("incoming");
  }, [refreshTransfers]);

  useEffect(() => {
    const listener = () => openMessages();
    window.addEventListener("file-transfers:open", listener);
    return () => window.removeEventListener("file-transfers:open", listener);
  }, [openMessages]);

  useEffect(() => {
    if (openMessagesRequest > handledMessagesReqRef.current) {
      handledMessagesReqRef.current = openMessagesRequest;
      queueMicrotask(openMessages);
    }
  }, [openMessagesRequest, openMessages]);

  useEffect(() => {
    if (openFilesRequest > handledFilesReqRef.current) {
      handledFilesReqRef.current = openFilesRequest;
      queueMicrotask(() => {
        setMessagesOpen(false);
        setTrashMode(false);
        refresh();
        onRefreshUsage();
      });
    }
  }, [openFilesRequest, refresh, onRefreshUsage]);

  const createFolder = () => {
    const name = newFolderName.trim();
    if (!name) return;
    void api.createStorageFolder(name, workspaceId, currentFolder?.id)
      .then(() => { setNewFolderName(""); refresh(); onRefreshUsage(); })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法新建文件夹"));
  };

  const uploadFiles = (event: ChangeEvent<HTMLInputElement>) => {
    const incoming = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!incoming.length) return;
    void (async () => {
      for (const file of incoming) {
        try {
          await api.uploadStorageFile(file, workspaceId, currentFolder?.id);
        } catch (reason) {
          setError(reason instanceof Error ? reason.message : `无法上传 ${file.name}`);
          break;
        }
      }
      refresh();
      onRefreshUsage();
    })();
  };

  const remove = (item: StorageFile) => {
    const request = trashMode ? api.permanentlyDeleteStorageFile(item.id, workspaceId) : api.deleteStorageFile(item.id, workspaceId);
    void request
      .then(() => { refresh(); onRefreshUsage(); })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法删除项目"));
  };

  const restore = (item: StorageFile) => {
    void api.restoreStorageFile(item.id, workspaceId)
      .then(() => { refresh(); onRefreshUsage(); })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法恢复项目"));
  };

  const beginRename = (item: StorageFile) => {
    setRenamingId(item.id);
    setRenameValue(item.name);
  };

  const saveRename = (item: StorageFile) => {
    const name = renameValue.trim();
    if (!name) return;
    void api.renameStorageFile(item.id, name, workspaceId)
      .then(() => { setRenamingId(null); refresh(); })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法重命名项目"));
  };

  const closeSend = () => {
    setSendFile(null);
    setRecipientIdentityId("");
    setRecipient(null);
    setSendMessage("");
    setSendIdempotencyKey("");
    setSendBusy(false);
    setDistributionDone(false);
    setDistributionHasFailures(false);
  };

  const beginSend = (item: StorageFile) => {
    setSendFile(item);
    setRecipient(null);
    setRecipientIdentityId("");
    setTeacherSendMode("single");
    setSendIdempotencyKey(createUuid());
    setSendMessage("");
    setDistributionDone(false);
    setDistributionHasFailures(false);
    if (isTeacher && typeof api.getDistributionTargets === "function") {
      void api.getDistributionTargets().then((res) => {
        setDistributionTargets(res);
        if (res?.classrooms && res.classrooms.length > 0 && !distributeClassroomId) {
          setDistributeClassroomId(res.classrooms[0].id);
        }
      }).catch(() => undefined);
    }
  };

  const distributeAsTeacher = async () => {
    if (!sendFile || sendBusy) return;
    setSendBusy(true);
    setSendMessage("");
    setDistributionHasFailures(false);
    try {
      let targetType: FileDistributionRequest["target_type"];
      let targetValue: string | null = null;
      if (teacherSendMode === "single") {
        targetType = "user";
        targetValue = recipient?.identity_id || recipientIdentityId.trim();
        if (!targetValue) {
          setSendMessage("请输入指定用户的身份 ID 或用户名");
          setSendBusy(false);
          return;
        }
      } else {
        targetType = distributeTargetType;
        if (distributeTargetType === "role") targetValue = distributeRole;
        else if (distributeTargetType === "classroom") targetValue = distributeClassroomId;
      }
      const res = await api.distributeFile({
        source_file_id: sendFile.id,
        target_type: targetType,
        target_value: targetValue,
        direct_delivery: directDelivery,
        workspace_id: workspaceId,
      });
      if (res.total_targets === 0) {
        setSendMessage("没有符合条件的接收人，请检查已选的班级或用户类型。");
        return;
      }
      const reasons = [...new Set(res.failures.map(({ reason }) => reason).filter(Boolean))];
      const failureDetails = reasons.length ? `原因：${reasons.slice(0, 3).join("；")}。` : "";
      if (res.success_count === 0) {
        setSendMessage(`发送失败，${res.failed_count} 人未收到。${failureDetails}`);
        return;
      }
      const delivery = res.direct_delivery ? "已送达" : "已发出接收请求";
      setDistributionDone(true);
      setDistributionHasFailures(res.failed_count > 0);
      setSendMessage(res.failed_count ? `${res.success_count} 人${delivery}，${res.failed_count} 人发送失败。${failureDetails}`
        : `已向 ${res.target_label} 发送，${res.success_count} 人${delivery}。`);
      window.dispatchEvent(new Event("file-transfers:changed"));
    } catch (reason: unknown) {
      setSendMessage(reason instanceof Error ? reason.message : "文件发送失败");
    } finally {
      setSendBusy(false);
    }
  };

  const lookupRecipient = async () => {
    const identityId = recipientIdentityId.trim();
    if (!identityId || sendBusy || !sendFile) return;
    setSendBusy(true);
    setRecipient(null);
    setSendMessage("");
    try {
      const found = await api.lookupTransferRecipient(identityId);
      const preflight = await api.preflightFileTransfer({ source_file_id: sendFile.id, recipient_identity_id: found.identity_id, workspace_id: workspaceId });
      if (!preflight.can_receive) {
        setSendMessage(preflight.reason || "对方当前无法接收此文件");
        return;
      }
      setRecipient(found);
    } catch (reason: unknown) {
      setSendMessage(reason instanceof Error ? reason.message : "未找到该身份 ID 对应的用户");
    } finally {
      setSendBusy(false);
    }
  };

  const sendToRecipient = async () => {
    if (!sendFile || !recipient || sendBusy) return;
    setSendBusy(true);
    try {
      await api.createFileTransfer({ source_file_id: sendFile.id, recipient_identity_id: recipient.identity_id, workspace_id: workspaceId, idempotency_key: sendIdempotencyKey || createUuid() });
      closeSend();
    } catch (reason: unknown) {
      setSendMessage(reason instanceof Error ? reason.message : "文件发送失败");
      setSendBusy(false);
    }
  };

  const actOnTransfer = async (transfer: FileTransfer, action: "accept" | "reject" | "cancel") => {
    setMessagesLoading(true);
    try {
      if (action === "accept") {
        await api.acceptFileTransfer(transfer.id);
        refresh();
      }
      else if (action === "reject") await api.rejectFileTransfer(transfer.id);
      else await api.cancelFileTransfer(transfer.id);
      refreshTransfers(messageBox);
      refreshTransferSummary();
      onRefreshUsage();
      window.dispatchEvent(new Event("file-transfers:changed"));
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "无法处理文件请求");
      setMessagesLoading(false);
    }
  };

  return <div className="storage-manager">
    <header className="storage-manager-toolbar">
      <div className="storage-breadcrumb" aria-label="当前文件夹路径">
        {trashMode ? <strong><Trash2 size={14} />回收站</strong> : <><button type="button" disabled={!folderPath.length} onClick={() => { setFolderPath([]); }}><FolderOpen size={14} />我的文件</button>{folderPath.map((folder, index) => <span key={folder.id}><span>/</span><button type="button" onClick={() => { setFolderPath(folderPath.slice(0, index + 1)); }}>{folder.name}</button></span>)}</>}
      </div>
      <div className="files-panel-actions">
        {!trashMode && folderPath.length > 0 && <button type="button" onClick={() => { setFolderPath(folderPath.slice(0, -1)); }}><ArrowLeft size={14} />返回</button>}
        {!trashMode && <><button type="button" className="storage-messages-button" onClick={openMessages}><Mail size={14} /><span>消息</span>{unreadTransfers > 0 && <span className="unread-red-badge" title={`${unreadTransfers} 条未读`}>{unreadTransfers > 99 ? "99+" : unreadTransfers}</span>}</button><button type="button" onClick={() => setNewFolderName((value) => value ? "" : "新建文件夹")}><FolderPlus size={14} />新建文件夹</button><button type="button" onClick={() => inputRef.current?.click()}><FileUp size={14} />上传</button></>}
        <button type="button" onClick={() => { setTrashMode((value) => !value); setFolderPath([]); setNewFolderName(""); }}><Trash2 size={14} />{trashMode ? "我的文件" : "回收站"}</button>
        <button type="button" aria-label="刷新文件列表" onClick={() => { refresh(); onRefreshUsage(); }}><RefreshCw size={14} /></button>
      </div>
      <input ref={inputRef} type="file" multiple onChange={uploadFiles} />
    </header>
    {!trashMode && newFolderName && <div className="storage-inline-form"><input autoFocus value={newFolderName} aria-label="新文件夹名称" onChange={(event) => setNewFolderName(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") createFolder(); if (event.key === "Escape") setNewFolderName(""); }} /><button type="button" onClick={createFolder}>创建</button><button type="button" onClick={() => setNewFolderName("")}>取消</button></div>}
    {error && <div className="files-panel-error" role="alert">{error}<button type="button" aria-label="关闭提示" onClick={() => setError("")}><X size={14} /></button></div>}
    {loading ? <div className="storage-empty">正在读取文件列表…</div> : items.length === 0 ? <div className="storage-empty"><FolderOpen size={24} /><strong>{trashMode ? "回收站为空" : "此文件夹为空"}</strong><span>{trashMode ? "删除的文件会在保留期后自动清理。" : "上传文件或新建文件夹，个人资料会和学习文档导入区分开。"}</span></div> : <div className="storage-list" role="list" aria-label={trashMode ? "回收站列表" : "个人文件列表"}>
      {items.map((item) => <div className="storage-row" key={item.id} role="listitem">
        <button type="button" className="storage-row-main" aria-label={item.kind === "file" ? `查看 ${item.name}` : `打开 ${item.name}`} title={item.kind === "file" ? `查看 ${item.name}` : `打开 ${item.name}`} onClick={() => {
          if (trashMode) return;
          if (item.kind === "folder") {
            setFolderPath([...folderPath, item]);
            return;
          }
          onOpenDocument(item);
        }}>
          {item.kind === "folder" ? <FolderOpen size={17} /> : <FileText size={17} />}<span><strong>{item.name}</strong><small>{item.kind === "folder" ? "文件夹" : formatBytes(item.size_bytes)} · 修改于 {formatModifiedAt(item.updated_at ?? item.created_at)}</small></span>
        </button>
        {trashMode ? <div className="storage-row-actions"><button type="button" aria-label={`恢复 ${item.name}`} onClick={() => restore(item)}><RotateCcw size={14} /></button><button type="button" className="danger" aria-label={`彻底删除 ${item.name}`} onClick={() => remove(item)}><Trash2 size={14} /></button></div> : renamingId === item.id ? <div className="storage-row-rename"><input autoFocus value={renameValue} aria-label={`重命名 ${item.name}`} onChange={(event) => setRenameValue(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") saveRename(item); if (event.key === "Escape") setRenamingId(null); }} /><button type="button" onClick={() => saveRename(item)}>保存</button></div> : <div className="storage-row-actions"><button type="button" aria-label={`重命名 ${item.name}`} onClick={() => beginRename(item)}><Pencil size={14} /></button>{item.kind === "file" && <><a href={storageFileDownloadUrl(item.id, workspaceId)} aria-label={`下载 ${item.name}`} download><Download size={14} /></a><button type="button" aria-label={`添加到对话 ${item.name}`} title="添加到当前对话" onClick={() => window.dispatchEvent(new CustomEvent("file-space:attach", { detail: { file: item, workspaceId } }))}><MessageSquarePlus size={14} /></button><button type="button" aria-label={`发送 ${item.name}`} onClick={() => beginSend(item)}><Send size={14} /></button></>}<button type="button" className="danger" aria-label={`删除 ${item.name}`} onClick={() => remove(item)}><Trash2 size={14} /></button></div>}
      </div>)}
    </div>}
    {messagesOpen && <section className="file-transfer-messages" aria-label="文件消息">
      <header>
        <div><span className="file-transfer-heading-icon"><Mail size={17} /></span><div><strong>文件消息</strong><small>收到与发送的文件请求</small></div></div>
        <button type="button" aria-label="关闭消息" onClick={() => setMessagesOpen(false)}><X size={15} /></button>
      </header>
      <div className="file-transfer-retention"><CalendarClock size={14} />消息记录自动保留 7 天，文件本身不会因此删除。</div>
      <nav aria-label="消息分类">
        <button type="button" className={messageBox === "incoming" ? "active" : ""} onClick={() => { setMessageBox("incoming"); refreshTransfers("incoming"); }}><span>收到的</span>{unreadTransfers > 0 && <em className="unread-red-badge">{unreadTransfers > 99 ? "99+" : unreadTransfers}</em>}</button>
        <button type="button" className={messageBox === "outgoing" ? "active" : ""} onClick={() => { setMessageBox("outgoing"); refreshTransfers("outgoing"); }}>已发送</button>
      </nav>
      {messagesLoading ? <div className="storage-empty" role="status">正在读取消息…</div> :
        transfers.length === 0 ? <div className="storage-empty"><Mail size={24} /><strong>暂无文件消息</strong><span>新的文件请求会显示在这里。</span></div> :
        <div className="file-transfer-list">{transfers.map((transfer) => {
          const incoming = messageBox === "incoming";
          const peer = incoming ? transfer.sender : transfer.recipient;
          const name = transfer.file_name ?? "未命名文件";
          const statusLabel = { pending: "待处理", accepted: "已接收", rejected: "已拒绝", cancelled: "已撤回", expired: "已过期", failed: "失败" }[transfer.status];
          const fileId = transfer.accepted_file_id;
          const fileWorkspaceId = transfer.recipient_workspace_id;
          const receivedDocument = incoming && transfer.status === "accepted" && fileId && fileWorkspaceId
            ? { id: fileId, name, size_bytes: transfer.size_bytes ?? 0, mime_type: null }
            : null;
          return <article key={transfer.id} className={incoming && transfer.is_unread ? "is-unread" : undefined}>
            <div className="file-transfer-item-main">
              <span className="file-transfer-file-icon">
                <FileText size={16} />
                {incoming && transfer.is_unread && <i className="file-transfer-unread-dot" aria-label="未读" />}
              </span>
              <div>
                <strong>{name}</strong>
                <span><UserRound size={12} />{incoming ? "来自" : "发送给"} {peer?.display_name ?? "已注销用户"}</span>
                <small>{transfer.size_bytes == null ? "大小未知" : formatBytes(transfer.size_bytes)} · {transfer.created_at ? `发送于 ${formatModifiedAt(transfer.created_at)}` : "发送时间未知"}{peer?.identity_id ? ` · ${peer.identity_id}` : ""}</small>
              </div>
            </div>
            <span className={`transfer-status ${transfer.status}`}>{statusLabel}</span>
            {transfer.status === "pending" && (incoming
              ? <footer>
                <button type="button" aria-label={`拒绝 ${name}`} onClick={() => void actOnTransfer(transfer, "reject")}>拒绝</button>
                <button type="button" className="primary" aria-label={`同意 ${name}`} onClick={() => void actOnTransfer(transfer, "accept")}>同意</button>
              </footer>
              : <footer><button type="button" aria-label={`撤回 ${name}`} onClick={() => void actOnTransfer(transfer, "cancel")}>撤回</button></footer>)}
            {receivedDocument && <footer>
              <button type="button" aria-label={`查看 ${name}`} onClick={() => onOpenDocument(receivedDocument, fileWorkspaceId ?? undefined)}>查看</button>
              <a href={storageFileDownloadUrl(receivedDocument.id, fileWorkspaceId ?? undefined)} aria-label={`下载 ${name}`} download>下载</a>
            </footer>}
          </article>;
        })}</div>}
    </section>}
    {sendFile && createPortal(
      <div className="file-transfer-overlay" role="presentation">
        <section
          className="file-transfer-dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby="file-transfer-title"
          style={isTeacher ? { maxWidth: 540, width: "95%" } : undefined}
        >
          <button type="button" className="file-transfer-close" aria-label="关闭发送窗口" onClick={closeSend}>
            <X size={16} />
          </button>
          <h3 id="file-transfer-title">{isTeacher ? "发送文件" : "发送文件"}</h3>
          <p>发送 <strong>{sendFile.name}</strong> ({formatBytes(sendFile.size_bytes)})</p>

          {isTeacher ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 14, margin: "14px 0" }}>
              {/* 单发 vs 群发 模式切换 */}
              <div style={{ display: "flex", gap: 8, padding: 3, background: "rgba(0, 0, 0, 0.05)", borderRadius: 10 }}>
                <button
                  type="button"
                  onClick={() => { setTeacherSendMode("single"); setSendMessage(""); }}
                  style={{
                    flex: 1,
                    padding: "8px 12px",
                    borderRadius: 7,
                    border: "none",
                    fontWeight: 600,
                    fontSize: 13,
                    cursor: "pointer",
                    background: teacherSendMode === "single" ? "var(--bg, #fff)" : "transparent",
                    color: teacherSendMode === "single" ? "var(--fg, #111)" : "var(--muted, #666)",
                    boxShadow: teacherSendMode === "single" ? "0 1px 3px rgba(0,0,0,0.1)" : "none",
                  }}
                >
                  单发（指定用户）
                </button>
                <button
                  type="button"
                  onClick={() => { setTeacherSendMode("group"); setSendMessage(""); }}
                  style={{
                    flex: 1,
                    padding: "8px 12px",
                    borderRadius: 7,
                    border: "none",
                    fontWeight: 600,
                    fontSize: 13,
                    cursor: "pointer",
                    background: teacherSendMode === "group" ? "var(--bg, #fff)" : "transparent",
                    color: teacherSendMode === "group" ? "var(--fg, #111)" : "var(--muted, #666)",
                    boxShadow: teacherSendMode === "group" ? "0 1px 3px rgba(0,0,0,0.1)" : "none",
                  }}
                >
                  群发（选择对象 / 用户类型）
                </button>
              </div>

              {teacherSendMode === "single" ? (
                <div>
                  <label htmlFor="file-transfer-recipient-id" style={{ display: "block", fontSize: 12, fontWeight: 650, marginBottom: 4 }}>
                    接收人身份 ID 或用户名
                  </label>
                  <div className="file-transfer-search">
                    <input
                      id="file-transfer-recipient-id"
                      autoFocus
                      placeholder="输入学号/身份 ID 或用户名"
                      value={recipientIdentityId}
                      onChange={(event) => {
                        setRecipientIdentityId(event.target.value);
                        setRecipient(null);
                        setSendMessage("");
                      }}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") void lookupRecipient();
                      }}
                    />
                    <button type="button" onClick={() => void lookupRecipient()} disabled={sendBusy || !recipientIdentityId.trim()}>
                      查询接收人
                    </button>
                  </div>
                  {recipient && (
                    <div className="file-transfer-recipient" style={{ marginTop: 6 }}>
                      <span>接收人</span>
                      <strong>{recipient.display_name}</strong>
                      <small>{recipient.identity_id}</small>
                    </div>
                  )}
                </div>
              ) : (
                <div>
                  <label style={{ display: "block", fontSize: 12, fontWeight: 650, marginBottom: 6 }}>
                    选择对象与用户类型
                  </label>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 8 }}>
                    <label
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                        padding: "8px 10px",
                        borderRadius: 6,
                        border: "1px solid var(--border-color, #ddd)",
                        background: distributeTargetType === "all_students" ? "rgba(59, 130, 246, 0.08)" : "transparent",
                        cursor: "pointer",
                        fontSize: 13,
                      }}
                    >
                      <input
                        type="radio"
                        name="distTarget"
                        checked={distributeTargetType === "all_students"}
                        onChange={() => setDistributeTargetType("all_students")}
                      />
                      <span>全体学生 ({distributionTargets?.summary.student_count ?? 0}人)</span>
                    </label>

                    <label
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                        padding: "8px 10px",
                        borderRadius: 6,
                        border: "1px solid var(--border-color, #ddd)",
                        background: distributeTargetType === "classroom" ? "rgba(59, 130, 246, 0.08)" : "transparent",
                        cursor: "pointer",
                        fontSize: 13,
                      }}
                    >
                      <input
                        type="radio"
                        name="distTarget"
                        checked={distributeTargetType === "classroom"}
                        onChange={() => setDistributeTargetType("classroom")}
                      />
                      <span>指定班级</span>
                    </label>

                    <label
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                        padding: "8px 10px",
                        borderRadius: 6,
                        border: "1px solid var(--border-color, #ddd)",
                        background: distributeTargetType === "role" ? "rgba(59, 130, 246, 0.08)" : "transparent",
                        cursor: "pointer",
                        fontSize: 13,
                      }}
                    >
                      <input
                        type="radio"
                        name="distTarget"
                        checked={distributeTargetType === "role"}
                        onChange={() => setDistributeTargetType("role")}
                      />
                      <span>某一角色类别</span>
                    </label>

                    <label
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                        padding: "8px 10px",
                        borderRadius: 6,
                        border: "1px solid var(--border-color, #ddd)",
                        background: distributeTargetType === "all_guests" ? "rgba(59, 130, 246, 0.08)" : "transparent",
                        cursor: "pointer",
                        fontSize: 13,
                      }}
                    >
                      <input
                        type="radio"
                        name="distTarget"
                        checked={distributeTargetType === "all_guests"}
                        onChange={() => setDistributeTargetType("all_guests")}
                      />
                      <span>全体游客 ({distributionTargets?.summary.guest_count ?? 0}人)</span>
                    </label>

                    <label
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                        padding: "8px 10px",
                        borderRadius: 6,
                        border: "1px solid var(--border-color, #ddd)",
                        background: distributeTargetType === "all_admins" ? "rgba(59, 130, 246, 0.08)" : "transparent",
                        cursor: "pointer",
                        fontSize: 13,
                      }}
                    >
                      <input
                        type="radio"
                        name="distTarget"
                        checked={distributeTargetType === "all_admins"}
                        onChange={() => setDistributeTargetType("all_admins")}
                      />
                      <span>全体管理员 ({distributionTargets?.summary.admin_count ?? 0}人)</span>
                    </label>

                    <label
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                        padding: "8px 10px",
                        borderRadius: 6,
                        border: "1px solid var(--border-color, #ddd)",
                        background: distributeTargetType === "all_users" ? "rgba(59, 130, 246, 0.08)" : "transparent",
                        cursor: "pointer",
                        fontSize: 13,
                      }}
                    >
                      <input
                        type="radio"
                        name="distTarget"
                        checked={distributeTargetType === "all_users"}
                        onChange={() => setDistributeTargetType("all_users")}
                      />
                      <span>全体用户 ({distributionTargets?.summary.total_user_count ?? 0}人)</span>
                    </label>
                  </div>

                  {distributeTargetType === "classroom" && (
                    <div style={{ marginTop: 8 }}>
                      <label style={{ display: "block", fontSize: 12, marginBottom: 4 }}>选择班级：</label>
                      <select
                        value={distributeClassroomId}
                        onChange={(e) => setDistributeClassroomId(e.target.value)}
                        style={{ width: "100%", padding: "6px 8px", borderRadius: 4, border: "1px solid #ccc" }}
                      >
                        {distributionTargets?.classrooms.map((c) => (
                          <option key={c.id} value={c.id}>
                            {c.name} ({c.student_count}人)
                          </option>
                        ))}
                      </select>
                    </div>
                  )}

                  {distributeTargetType === "role" && (
                    <div style={{ marginTop: 8 }}>
                      <label style={{ display: "block", fontSize: 12, marginBottom: 4 }}>选择角色类别：</label>
                      <select
                        value={distributeRole}
                        onChange={(e) => setDistributeRole(e.target.value)}
                        style={{ width: "100%", padding: "6px 8px", borderRadius: 4, border: "1px solid #ccc" }}
                      >
                        {distributionTargets?.roles.map((r) => (
                          <option key={r.code} value={r.code}>
                            {r.name}
                          </option>
                        ))}
                      </select>
                    </div>
                  )}
                </div>
              )}

              {/* 发送模式选择 */}
              <div style={{ marginTop: 4 }}>
                <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={directDelivery}
                    onChange={(e) => setDirectDelivery(e.target.checked)}
                  />
                  <span>
                    <strong>直接存入接收方文件列表</strong> (无需对方确认，即刻可用)
                  </span>
                </label>
              </div>
            </div>
          ) : (
            <>
              <label htmlFor="file-transfer-recipient-id">接收人身份 ID</label>
              <div className="file-transfer-search">
                <input
                  id="file-transfer-recipient-id"
                  autoFocus
                  value={recipientIdentityId}
                  onChange={(event) => {
                    setRecipientIdentityId(event.target.value);
                    setRecipient(null);
                    setSendMessage("");
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void lookupRecipient();
                  }}
                />
                <button type="button" onClick={() => void lookupRecipient()} disabled={sendBusy || !recipientIdentityId.trim()}>
                  查询接收人
                </button>
              </div>
              {recipient && (
                <div className="file-transfer-recipient">
                  <span>接收人</span>
                  <strong>{recipient.display_name}</strong>
                  <small>{recipient.identity_id}</small>
                </div>
              )}
            </>
          )}

          {sendMessage && (
            <div
              className={distributionDone && !distributionHasFailures ? "files-panel-notice" : "files-panel-error"}
              role="alert"
              style={
                distributionDone && !distributionHasFailures
                  ? { background: "rgba(16, 185, 129, 0.1)", color: "#059669", padding: "8px 12px", borderRadius: 6 }
                  : undefined
              }
            >
              {sendMessage}
            </div>
          )}

          <footer>
            <button type="button" onClick={closeSend}>
              {distributionDone ? "关闭" : "取消"}
            </button>
            <button
              type="button"
              className="primary"
              disabled={
                sendBusy ||
                distributionDone ||
                (isTeacher
                  ? teacherSendMode === "single" && !recipientIdentityId.trim()
                  : !recipient)
              }
              onClick={() => void (isTeacher ? distributeAsTeacher() : sendToRecipient())}
            >
              {sendBusy ? "正在发送…" : isTeacher ? (teacherSendMode === "single" ? "确认发送" : "确认群发") : "确认发送"}
            </button>
          </footer>
        </section>
      </div>,
      document.body
    )}
  </div>;
}

function ScopedFilesPanel({
  userId,
  workspaceId,
  previewRequest,
  openMessagesRequest = 0,
  openFilesRequest = 0,
}: {
  userId?: string | null;
  workspaceId?: string;
  previewRequest?: FilesPanelPreviewRequest | null;
  openMessagesRequest?: number;
  openFilesRequest?: number;
}) {
  const [tab, setTab] = useState<"manager" | "import">(() => previewRequest ? "import" : "manager");
  const [storagePreviewRequest, setStoragePreviewRequest] = useState<FilesPanelPreviewRequest | null>(null);
  const [previousPreviewRequest, setPreviousPreviewRequest] = useState(previewRequest);
  const handledMessagesReqRef = useRef(0);
  const handledFilesReqRef = useRef(0);
  const [usage, setUsage] = useState<StorageUsage | null>(null);
  const [usageError, setUsageError] = useState("");

  const refreshUsage = useCallback(() => {
    void api.getStorageUsage(workspaceId)
      .then((value) => { setUsage(value); setUsageError(""); })
      .catch((reason: unknown) => setUsageError(reason instanceof Error ? reason.message : "无法读取空间用量"));
  }, [workspaceId]);

  useEffect(() => { refreshUsage(); }, [refreshUsage]);

  if (previousPreviewRequest !== previewRequest) {
    setPreviousPreviewRequest(previewRequest);
    setStoragePreviewRequest(null);
    if (previewRequest) setTab("import");
  }

  useEffect(() => {
    if (openMessagesRequest > handledMessagesReqRef.current) {
      handledMessagesReqRef.current = openMessagesRequest;
      queueMicrotask(() => setTab("manager"));
    }
  }, [openMessagesRequest]);

  useEffect(() => {
    if (openFilesRequest > handledFilesReqRef.current) {
      handledFilesReqRef.current = openFilesRequest;
      queueMicrotask(() => setTab("manager"));
    }
  }, [openFilesRequest]);

  useEffect(() => {
    const onOpenTransfers = () => setTab("manager");
    window.addEventListener("file-transfers:open", onOpenTransfers);
    return () => window.removeEventListener("file-transfers:open", onOpenTransfers);
  }, []);

  const openStorageDocument = useCallback((item: StorageDocument, fileWorkspaceId = workspaceId) => {
    setStoragePreviewRequest({
      id: item.id,
      name: item.name,
      url: "",
      mediaType: item.mime_type ?? "text/plain",
      bytes: item.size_bytes,
      source: "storage",
      workspaceId: fileWorkspaceId,
    });
    setTab("import");
  }, [workspaceId]);

  return <section className="files-panel files-manager-shell" aria-label="文件工具">
    <div className="storage-summary">
      <div className="storage-summary-heading"><div><FolderOpen size={17} /><strong>文件空间</strong><small>{usage ? `${usage.role} · ${usage.files_count}/${usage.max_items} 个文件` : "正在读取空间用量…"}</small></div><button type="button" aria-label="刷新空间用量" onClick={refreshUsage}><RefreshCw size={14} /></button></div>
      {usage ? <><StorageMeter label="通用空间" bucket={usage.core} /><StorageMeter label="个人文件" bucket={usage.files} /></> : usageError ? <div className="storage-summary-error">{usageError}</div> : <div className="storage-meter-skeleton" />}
    </div>
    <div className="files-panel-tabs" role="tablist" aria-label="文件页面">
      <button type="button" role="tab" aria-selected={tab === "manager"} onClick={() => setTab("manager")}>我的文件</button>
      <button type="button" role="tab" aria-selected={tab === "import"} onClick={() => { setStoragePreviewRequest(null); setTab("import"); }}>学习文档导入</button>
    </div>
    <div className="files-view" hidden={tab !== "manager"}><StorageManager workspaceId={workspaceId} onRefreshUsage={refreshUsage} onOpenDocument={openStorageDocument} openMessagesRequest={openMessagesRequest} openFilesRequest={openFilesRequest} /></div>
    <div className="files-view" hidden={tab !== "import"}><LearningImportPanel userId={userId ?? null} workspaceId={workspaceId ?? "default"} previewRequest={storagePreviewRequest ?? previewRequest ?? null} /></div>
  </section>;
}


/** Open documents and local caches belong to exactly one account/workspace. */
export function FilesPanel(props: {
  userId?: string | null;
  workspaceId?: string;
  previewRequest?: FilesPanelPreviewRequest | null;
  openMessagesRequest?: number;
  openFilesRequest?: number;
} = {}) {
  return <ScopedFilesPanel key={`${props.userId ?? "anonymous"}:${props.workspaceId ?? "default"}`} {...props} />;
}
