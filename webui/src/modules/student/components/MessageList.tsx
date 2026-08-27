import { Check, Copy, GraduationCap, RotateCcw } from "lucide-react";
import { useState } from "react";

import { ActivityPanel } from "./ActivityPanel";
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
function AssistantMessage({ message, showReasoning, onFollowUp }: {
  message: ChatMessage;

  showReasoning: boolean;
  onFollowUp: (text: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const streaming = ["accepted", "running"].includes(message.status ?? "");
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
        {message.status === "failed" ? (
          <div className="error-card">这次讲解没有完成，请稍后重试。</div>
        ) : message.status === "cancelled" && !message.content ? (
          <div className="muted-card">已停止生成。</div>
        ) : (
          <MarkdownContent streaming={streaming}>{message.content}</MarkdownContent>
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
}

function UserMessage({ message }: { message: ChatMessage }) {
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
        <div className="message-attachments" style={{ display: "flex", gap: "8px", marginBottom: content ? "8px" : 0 }}>
          {attachments.map((att, i) => (
            <a key={i} href={att.url || "#"} target="_blank" rel="noopener noreferrer" style={{ display: "inline-block" }}>
              {att.url ? <img src={att.url} alt={att.displayName ?? att.fileName} style={{ width: 60, height: 60, objectFit: "cover", borderRadius: "4px" }} /> : <span>{att.displayName ?? att.fileName}</span>}
            </a>
          ))}
        </div>
      )}
      {content}
    </div>
  );
}

export function MessageList({ messages, loading, showReasoning, onFollowUp }: {
  messages: ChatMessage[];
  loading: boolean;
  showReasoning: boolean;
  onFollowUp: (text: string) => void;
}) {
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
        <UserMessage key={message.id} message={message} />
      ) : (
        <AssistantMessage
  key={message.id}
  message={message}
  showReasoning={showReasoning}
  onFollowUp={onFollowUp}
/>
      ))}
    </div>
  );
}
