import { ArrowUp, GraduationCap, Plus, RotateCcw, Square, X } from "lucide-react";
import { useState, useRef, type KeyboardEvent, type ReactNode } from "react";

import { uploadAttachment } from "@/platform/http/api";
import type { ChatAttachment } from "@/shared/types";
import { createUuid } from "@/shared/utils/uuid";

const prompts = ["用简单语言解释", "举一个实际例子", "逐步推导", "对比两个概念", "出一道练习题", "检查我的答案"];

interface ComposerAttachment extends ChatAttachment {
  clientId: string;
  sourceFile: File;
}

function uploadErrorMessage(reason: unknown): string {
  if (reason instanceof Error && typeof (reason as { status?: unknown }).status === "number" && reason.message.trim()) {
    return reason.message;
  }
  return "上传失败，请检查网络后重试";
}

export function Composer({ sessionId, disabled, running, centered = false, onSend, onCancel, contextControl }: {
  sessionId?: string | null;
  disabled: boolean;
  running: boolean;
  centered?: boolean;
  onSend: (content: string, attachments?: ChatAttachment[]) => void;
  onCancel: () => void;
  contextControl?: ReactNode;
}) {
  const [content, setContent] = useState("");
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const attachmentsReady = attachments.every((attachment) => attachment.status === "ready");
  const readyAttachments = attachments.filter((attachment) => attachment.status === "ready");

  const sendValue = (value: string) => {
    const trimmed = value.trim();
    if ((!trimmed && readyAttachments.length === 0) || !attachmentsReady || disabled || running) return;
    setContent("");
    if (readyAttachments.length > 0) {
      onSend(trimmed, readyAttachments);
    } else {
      onSend(trimmed);
    }
    setAttachments([]);
  };
  const submit = () => sendValue(content);
  const submitPrompt = (prompt: string) => {
    // Preserve any in-progress input by appending the preset on a new line,
    // then flow through the shared sendValue path for trim/guard handling.
    const existing = content.trim();
    sendValue(existing ? `${existing}\n${prompt}` : prompt);
  };
  const keyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };
  const upload = async (attachment: ComposerAttachment) => {
    if (!sessionId) return;
    setAttachments((current) => current.map((item) => item.clientId === attachment.clientId
      ? { ...item, status: "uploading", errorMessage: undefined }
      : item));
    try {
      const response = await uploadAttachment(sessionId, attachment.sourceFile);
      if (attachment.url.startsWith("blob:") && typeof URL.revokeObjectURL === "function") {
        URL.revokeObjectURL(attachment.url);
      }
      setAttachments((current) => current.map((item) => item.clientId === attachment.clientId ? {
        ...item,
        fileName: response.file_name,
        url: response.url,
        mediaType: response.media_type,
        width: response.width,
        height: response.height,
        status: "ready",
        errorMessage: undefined,
      } : item));
    } catch (reason) {
      setAttachments((current) => current.map((item) => item.clientId === attachment.clientId
        ? { ...item, status: "error", errorMessage: uploadErrorMessage(reason) }
        : item));
    }
  };
  const removeAttachment = (clientId: string) => {
    setAttachments((current) => {
      const removed = current.find((item) => item.clientId === clientId);
      if (removed?.url.startsWith("blob:") && typeof URL.revokeObjectURL === "function") {
        URL.revokeObjectURL(removed.url);
      }
      return current.filter((item) => item.clientId !== clientId);
    });
  };
  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file || !sessionId) return;
    event.target.value = "";

    const newAttachment: ComposerAttachment = {
      clientId: createUuid(),
      sourceFile: file,
      fileName: "",
      displayName: file.name,
      url: URL.createObjectURL(file),
      mediaType: file.type,
      width: 0,
      height: 0,
      status: "uploading",
    };

    setAttachments((prev) => [...prev, newAttachment]);
    void upload(newAttachment);
  };

  return <div className={`composer-wrap ${centered ? "centered" : ""}`}>
    <div className="quick-prompts">{prompts.map((prompt) => <button key={prompt} type="button" onClick={() => submitPrompt(prompt)} disabled={disabled || running || !attachmentsReady}>{prompt}</button>)}</div>
    <div className="composer">
      {attachments.length > 0 && (
        <div className="composer-attachments" style={{ display: "flex", gap: "8px", padding: "8px", borderBottom: "1px solid var(--border)", overflowX: "auto" }}>
          {attachments.map((att) => (
            <div key={att.clientId} className="attachment-thumbnail" style={{ position: "relative", display: "inline-block" }}>
              <img src={att.url} alt={att.displayName ?? att.fileName} style={{ width: 60, height: 60, objectFit: "cover", opacity: att.status === "uploading" ? 0.5 : 1, borderRadius: "4px" }} />
              {att.status === "error" && <span role="status" style={{ color: "red", position: "absolute", bottom: 0, left: 0, fontSize: "10px", background: "rgba(255,255,255,0.8)", padding: "2px" }}>{att.errorMessage}</span>}
              {att.status === "error" && <button type="button" onClick={() => void upload(att)} aria-label={`重试附件 ${att.displayName ?? att.fileName}`} style={{ position: "absolute", right: 2, bottom: 2, display: "flex", padding: 2 }}><RotateCcw size={12} /></button>}
              <button type="button" onClick={() => removeAttachment(att.clientId)} aria-label={`移除附件 ${att.displayName ?? att.fileName}`} style={{ position: "absolute", right: 2, top: 2, display: "flex", padding: 2 }}><X size={12} /></button>
            </div>
          ))}
        </div>
      )}
      <textarea value={content} onChange={(event) => setContent(event.target.value)} onKeyDown={keyDown} disabled={disabled} rows={centered ? 3 : 1} placeholder="问一个 NLP 问题……" aria-label="学习问题" />
      <div className="composer-toolbar">
        <input type="file" ref={fileInputRef} hidden accept="image/jpeg,image/png,image/webp" onChange={handleFileSelect} />
        <button type="button" className="attachment-button" style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-muted)", padding: "4px", display: "flex", alignItems: "center" }} onClick={() => fileInputRef.current?.click()} disabled={disabled || running || !sessionId} aria-label="上传附件"><Plus size={18} /></button>
        <span><GraduationCap size={15} />Nova · LSNU NLP Learning Agent</span>
        {contextControl}
        {running ? <button className="send-button stop" type="button" onClick={onCancel} aria-label="停止生成"><Square size={14} fill="currentColor" /></button> : <button className="send-button" type="button" onClick={submit} disabled={disabled || !attachmentsReady || (!content.trim() && readyAttachments.length === 0)} aria-label="发送"><ArrowUp size={18} /></button>}
      </div>
    </div>
    <p className="composer-hint">Nova 也可能犯错，重要结论请结合教材验证</p>
  </div>;
}
