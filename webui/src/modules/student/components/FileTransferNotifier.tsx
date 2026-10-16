import { Inbox, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api } from "@/platform/http/api";

const POLL_INTERVAL_MS = 15_000;

export function FileTransferNotifier({ userId, onOpenMessages }: { userId: string; onOpenMessages: () => void }) {
  const [pendingCount, setPendingCount] = useState(0);
  const [open, setOpen] = useState(false);
  const remindedVersion = useRef<number | null>(null);

  useEffect(() => {
    let active = true;
    const refresh = () => {
      if (typeof api.getFileTransferSummary !== "function") return;
      void api.getFileTransferSummary().then(({ pending_count, notification_version }) => {
        if (!active) return;
        setPendingCount(pending_count);
        const hasNewRequest = remindedVersion.current === null || notification_version > remindedVersion.current;
        remindedVersion.current = notification_version;
        if (pending_count > 0 && hasNewRequest) {
          setOpen(true);
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

  if (!open || pendingCount < 1) return null;
  return <div className="file-transfer-overlay"><section className="file-transfer-notice" role="dialog" aria-modal="true" aria-labelledby="file-transfer-notice-title"><button className="file-transfer-close" type="button" aria-label="关闭文件请求提醒" onClick={() => setOpen(false)}><X size={16} /></button><span className="file-transfer-notice-icon"><Inbox size={24} /></span><h3 id="file-transfer-notice-title">收到 {pendingCount} 个文件请求</h3><p>发送方正在等待你同意或拒绝。请求会保留 7 天。</p><button type="button" className="primary" onClick={() => { setOpen(false); onOpenMessages(); }}>查看消息</button></section></div>;
}
