import * as Dialog from "@radix-ui/react-dialog";
import { Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "@/platform/http/api";
import type { ReleaseNoteEntry } from "@/shared/types";

export function ReleaseUpdateNotice({ userId, paused = false }: { userId?: string; paused?: boolean }) {
  const [notice, setNotice] = useState<{ userId: string; note: ReleaseNoteEntry } | null>(null);
  const acknowledged = useRef<string | null>(null);
  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    void api.getUnreadReleaseNote().then(({ item }) => {
      if (!cancelled) setNotice(item ? { userId, note: item } : null);
    }).catch(() => { /* An announcement must never interrupt login or chat. */ });
    return () => { cancelled = true; };
  }, [userId]);

  const current = notice?.userId === userId ? notice : null;
  const open = !!current && !paused;
  useEffect(() => {
    if (!open || !current) return;
    const key = `${current.userId}:${current.note.id}`;
    if (acknowledged.current === key) return;
    acknowledged.current = key;
    void api.markReleaseNoteRead(current.note.id).catch(() => {
      // Keep this view open; a later login can retry the durable receipt.
      if (acknowledged.current === key) acknowledged.current = null;
    });
  }, [open, current]);

  return <Dialog.Root open={open} onOpenChange={(next) => { if (!next) setNotice(null); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="login-dialog-overlay release-update-overlay" onPointerDown={(event) => {
        if (event.target === event.currentTarget) setNotice(null);
      }} />
      <Dialog.Content className="release-update-card">
        <Dialog.Close className="login-dialog-close" aria-label="关闭更新说明"><X size={19} /></Dialog.Close>
        <header className="release-update-header">
          <span className="release-update-icon"><Sparkles size={24} aria-hidden="true" /></span>
          <div><p className="release-update-eyebrow">NOVA · 有新变化啦</p>
            <Dialog.Title>本次更新与修复</Dialog.Title>
          </div>
        </header>
        <Dialog.Description className="release-update-meta">
          <strong>v{current?.note.version}</strong><span>{current?.note.released_at.slice(0, 10)}</span>
        </Dialog.Description>
        <div className="release-update-body">
          <ul>{current?.note.notes.map((item, index) => <li key={index}>{item}</li>)}</ul>
        </div>
        <footer className="release-update-footer">
          <p>以后可在「设置 → 版本与更新」查看</p>
          <Dialog.Close className="release-update-confirm">知道啦</Dialog.Close>
        </footer>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
