import { Inbox, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api, type FileTransferUnreadSummary } from "@/platform/http/api";

const POLL_INTERVAL_MS = 15_000;

export async function readFileTransferUnreadSummary(): Promise<FileTransferUnreadSummary | null> {
  if (typeof api.getFileTransferUnreadSummary === "function") {
    return api.getFileTransferUnreadSummary();
  }
  if (typeof api.getFileTransferSummary === "function") {
    const base = await api.getFileTransferSummary();
    return {
      pending_count: base.pending_count,
      unseen_accepted_count: 0,
      unread_count: base.pending_count,
      notification_version: base.notification_version,
    };
  }
  return null;
}

export function FileTransferNotifier({
  userId,
  onOpenMessages,
  onOpenFiles,
  onUnreadCountChange,
}: {
  userId: string;
  onOpenMessages: () => void;
  onOpenFiles?: () => void;
  onUnreadCountChange?: (count: number) => void;
}) {
  const [summary, setSummary] = useState<FileTransferUnreadSummary>({
    pending_count: 0,
    unseen_accepted_count: 0,
    unread_count: 0,
    notification_version: 0,
  });
  const [open, setOpen] = useState(false);
  const remindedVersion = useRef<number | null>(null);
  const onUnreadCountChangeRef = useRef(onUnreadCountChange);

  useEffect(() => {
    onUnreadCountChangeRef.current = onUnreadCountChange;
  }, [onUnreadCountChange]);

  useEffect(() => {
    let active = true;
    const refresh = () => {
      void readFileTransferUnreadSummary().then((next) => {
        if (!active || !next) return;
        setSummary(next);
        onUnreadCountChangeRef.current?.(next.unread_count);
        const previousVersion = remindedVersion.current;
        const hasNewNotification = previousVersion === null || next.notification_version > previousVersion;
        remindedVersion.current = next.notification_version;
        if (next.unread_count > 0 && hasNewNotification) {
          setOpen(true);
          if (previousVersion !== null && next.unseen_accepted_count > 0) {
            window.dispatchEvent(new Event("sandbox:files-changed"));
          }
        } else if (next.unread_count === 0) {
          setOpen(false);
        }
      }).catch(() => undefined);
    };
    remindedVersion.current = null;
    refresh();
    const interval = window.setInterval(refresh, POLL_INTERVAL_MS);
    const onChanged = () => refresh();
    window.addEventListener("file-transfers:changed", onChanged);
    return () => {
      active = false;
      window.clearInterval(interval);
      window.removeEventListener("file-transfers:changed", onChanged);
    };
  }, [userId]);

  if (!open || summary.unread_count < 1) return null;

  const { pending_count: pendingCount, unseen_accepted_count: unseenAcceptedCount, unread_count: totalUnread } = summary;
  const title = pendingCount > 0 && unseenAcceptedCount > 0
    ? `收到 ${totalUnread} 条新文件消息`
    : pendingCount > 0
      ? `收到 ${pendingCount} 个文件请求`
      : `收到 ${unseenAcceptedCount} 个新文件`;
  const description = pendingCount > 0 && unseenAcceptedCount > 0
    ? `含 ${pendingCount} 个待处理请求，${unseenAcceptedCount} 个文件已存入「我的文件」。`
    : pendingCount > 0
      ? "发送方正在等待你同意或拒绝。请求会保留 7 天。"
      : "已直接存入你的「我的文件」，可随时打开或下载。";

  const handleViewFiles = () => {
    setOpen(false);
    if (typeof api.markFileTransfersSeen === "function") {
      void api.markFileTransfersSeen()
        .then(() => {
          window.dispatchEvent(new Event("file-transfers:changed"));
        })
        .catch(() => undefined);
    }
    onOpenFiles?.();
  };

  return (
    <aside
      className="file-transfer-toast"
      role="status"
      aria-live="polite"
      aria-labelledby="file-transfer-notice-title"
    >
      <span className="file-transfer-toast-icon" aria-hidden="true">
        <Inbox size={20} />
      </span>
      <div className="file-transfer-toast-body">
        <div className="file-transfer-toast-head">
          <strong id="file-transfer-notice-title">{title}</strong>
          <button
            className="file-transfer-toast-close"
            type="button"
            aria-label="关闭文件请求提醒"
            onClick={() => setOpen(false)}
          >
            <X size={15} />
          </button>
        </div>
        <p>{description}</p>
        <div className="file-transfer-toast-actions">
          {unseenAcceptedCount > 0 && onOpenFiles && (
            <button type="button" onClick={handleViewFiles}>
              查看我的文件
            </button>
          )}
          <button
            type="button"
            className="primary"
            onClick={() => {
              setOpen(false);
              onOpenMessages();
            }}
          >
            查看消息
          </button>
        </div>
      </div>
    </aside>
  );
}
