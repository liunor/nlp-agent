import { Check, Copy, GraduationCap, RotateCcw } from "lucide-react";
import { memo, useEffect, useState } from "react";

import { ActivityPanel } from "./ActivityPanel";
import { ImagePreviewDialog } from "./ImagePreviewDialog";
import { MarkdownContent, stripInternalChatMetadata } from "./MarkdownContent";
import type { ChatMessage } from "@/shared/types";


export async function copyTextToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // Fall back when Clipboard API access is denied.
    }
  }

  const textarea = document.createElement("textarea");
  const activeElement =
    document.activeElement instanceof HTMLElement ? document.activeElement : null;

  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  document.body.appendChild(textarea);

  try {
    textarea.focus();
    textarea.select();

    if (!document.execCommand("copy")) {
      throw new Error("Unable to copy conversation");
    }
  } finally {
    textarea.remove();
    activeElement?.focus();
  }
}
const AssistantMessage = memo(function AssistantMessage({ message, showReasoning, streamRenderIntervalMs, onFollowUp }: {
  message: ChatMessage;

  showReasoning: boolean;
  streamRenderIntervalMs: number;
  onFollowUp: (text: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const [clockMs, setClockMs] = useState(() => Date.now());
  const streaming = ["accepted", "running"].includes(message.status ?? "");
  useEffect(() => {
    if (message.status !== "accepted") return;
    const timer = window.setInterval(() => setClockMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [message.status]);
  const queueSnapshotMs = Date.parse(message.queueObservedAt ?? message.createdAt);
  const queueWaitedS = Math.max(
    0,
    (message.queueWaitedS ?? 0) + Math.floor((clockMs - queueSnapshotMs) / 1000),
  );
  const copy = async () => {
    try {
      await copyTextToClipboard(
        stripInternalChatMetadata(message.content).trim()
      );
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  };
  return (
    <article className="assistant-message">
      <div className="assistant-mark"><GraduationCap size={17} /></div>
      <div className="assistant-body">
        <ActivityPanel
          activities={message.activities ?? []}
          reasoning={message.reasoning}
          showReasoning={showReasoning}
          running={streaming}
          startedAt={message.startedAt}
          completedAt={message.completedAt}
        />
        {message.status === "accepted" && (
          <div className="queue-status" role="status">
            排队中{message.queuePosition ? ` · 前方 ${Math.max(0, message.queuePosition - 1)} 个任务` : ""}
            {` · 已等待 ${queueWaitedS} 秒`}
          </div>
        )}
        {message.status === "failed" && !message.content ? (
          <div className="error-card">这次讲解没有完成，请稍后重试。</div>
        ) : message.status === "cancelled" && !message.content ? (
          <div className="muted-card">已停止生成。</div>
        ) : (
          <>
            <MarkdownContent streaming={streaming} streamRenderIntervalMs={streamRenderIntervalMs}>{message.content}</MarkdownContent>
            {message.status === "failed" && message.content && (
              <div className="error-card">这次讲解未完整完成，已保留已生成内容，请稍后重试。</div>
            )}
          </>
        )}
        {!streaming && message.content && (
          <div className="message-actions">
            <button type="button" onClick={copy}>{copied ? <Check size={15} /> : <Copy size={15} />} {copied ? "已复制" : "复制"}</button>
            <button type="button" onClick={() => onFollowUp("请换一种更容易理解的方式重新解释。") }><RotateCcw size={15} /> 换种讲法</button>
          </div>
        )}
      </div>
    </article>
  );
});

const UserMessage = memo(function UserMessage({
  message,
  onPreviewImage,
  onDismissRejected,
}: {
  message: ChatMessage;
  onPreviewImage?: (att: { url: string; alt?: string }) => void;
  onDismissRejected?: (id: string) => void;
}) {
  let content = message.content;
  const attachments = [...(message.attachments || [])];

  const markerIdx = content.indexOf("---附件---");
  if (markerIdx !== -1) {
    const attachmentBlock = content.slice(markerIdx);
    content = content.slice(0, markerIdx).trim();
    if (attachments.length === 0) {
      const regex = /!?\[([^\]]+)\]\(([^)]+)\)/g;
      let match;
      while ((match = regex.exec(attachmentBlock)) !== null) {
        attachments.push({
          fileName: match[1],
          url: match[2],
          mediaType: "image/jpeg",
          width: 0,
          height: 0,
          status: "ready"
        });
      }
    }
  }

  return (
    <div className="user-message">
      {attachments.length > 0 && (
        <div className={`message-attachments${content ? " has-content" : ""}`}>
          {attachments.map((att, i) => {
            const displayName = att.displayName ?? att.fileName;
            return att.url ? (
              <button
                key={i}
                type="button"
                className="message-attachment-trigger"
                onClick={() => onPreviewImage?.({ url: att.url, alt: displayName })}
                aria-label={`查看原图：${displayName}`}
              >
                <img
                  src={att.url}
                  alt={displayName}
                  className="message-attachment-image"
                  loading="eager"
                />
              </button>
            ) : (
              <span key={i} className="message-attachment-fallback">
                {displayName}
              </span>
            );
          })}
        </div>
      )}
      {content}
      {message.status === "failed" && <div role="status">发送失败，问题未提交。请复制内容后重试。
        {onDismissRejected && <button type="button" onClick={() => onDismissRejected(message.id)}>移除未提交的问题</button>}
      </div>}
    </div>
  );
});

export function MessageList({ messages, loading, showReasoning, streamRenderIntervalMs = 30, onFollowUp, onDismissRejected }: {
  messages: ChatMessage[];
  loading: boolean;
  showReasoning: boolean;
  streamRenderIntervalMs?: number;
  onFollowUp: (text: string) => void;
  onDismissRejected?: (id: string) => void;
}) {
  const [previewAttachment, setPreviewAttachment] = useState<{ url: string; alt?: string } | null>(null);

  if (loading) return <div className="empty-state"><span className="loading-dot" />正在加载学习记录…</div>;
  if (!messages.length) {
    return (
      <div className="hero-state">
        <div className="hero-icon"><GraduationCap size={30} /></div>
        <h2>今天想学习什么？</h2>
        <p>可以询问 NLP 概念、公式推导、模型原理，也可以让我出题并检查答案。</p>
      </div>
    );
  }

  return (
    <div className="message-list">
      {messages.map((message) => message.role === "user" ? (
        <UserMessage
          key={message.id}
          message={message}
          onPreviewImage={setPreviewAttachment}
          onDismissRejected={onDismissRejected}
        />
      ) : (
        <AssistantMessage
          key={message.id}
          message={message}
          showReasoning={showReasoning}
          streamRenderIntervalMs={streamRenderIntervalMs}
          onFollowUp={onFollowUp}
        />
      ))}
      <ImagePreviewDialog
        open={Boolean(previewAttachment)}
        url={previewAttachment?.url}
        alt={previewAttachment?.alt}
        onClose={() => setPreviewAttachment(null)}
      />
    </div>
  );
}
