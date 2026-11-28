import { AttachmentCard } from "./AttachmentCard";
import { AttachmentMenu } from "./AttachmentMenu";
import { FileSpacePicker } from "./FileSpacePicker";
import { fileSpaceViewUrl } from "@/shared/utils/file-reference";
import { UploadQueue } from "./upload-queue";
import { STUDENT_AGENT_LABEL } from "@/shared/branding";
import { ArrowUp, GraduationCap, Square } from "lucide-react";
import { useEffect, useState, useRef, type ChangeEvent, type ClipboardEvent, type KeyboardEvent, type ReactNode } from "react";

import { api, uploadAttachment, type StorageFile } from "@/platform/http/api";
import type { ChatAttachment } from "@/shared/types";
import { createUuid } from "@/shared/utils/uuid";

const prompts = ["用简单语言解释", "举一个实际例子", "逐步推导", "对比两个概念", "出一道练习题", "检查我的答案"];
const MAX_ATTACHMENTS = 5;
const ATTACHMENT_LIMIT_NOTICE = `每条消息最多添加 ${MAX_ATTACHMENTS} 个附件`;

interface ComposerAttachment extends ChatAttachment {
  clientId: string;
  sourceFile: File;
  sizeBytes?: number;
  ownerSessionId: string | null;
  abort: AbortController;
}

function pastedImageName(file: File, index: number): string {
  if (file.name.trim()) return file.name;
  const extension = file.type === "image/jpeg"
    ? "jpg"
    : file.type.startsWith("image/")
      ? file.type.slice("image/".length).replace("+xml", "") || "png"
      : "png";
  return `pasted-image${index === 0 ? "" : `-${index + 1}`}.${extension}`;
}

function uploadErrorMessage(reason: unknown): string {
  if (reason instanceof Error && typeof (reason as { status?: unknown }).status === "number" && reason.message.trim()) {
    return reason.message;
  }
  return "上传失败，请检查网络后重试";
}

export function Composer({ sessionId, workspaceId, disabled, sendDisabled = false, selectionNotice, thinkingControl, running, cancelling = false, centered = false, onSend, onCancel, onEnsureSession, contextControl }: {
  sessionId?: string | null;
  workspaceId?: string;
  disabled: boolean;
  sendDisabled?: boolean;
  selectionNotice?: string;
  running: boolean;
  cancelling?: boolean;
  centered?: boolean;
  onSend: (content: string, attachments?: ChatAttachment[]) => void;
  onCancel: () => void;
  onEnsureSession?: () => Promise<string | null>;
  contextControl?: ReactNode;
  thinkingControl?: ReactNode;
}) {
  const [content, setContent] = useState("");
  const [spaceOpen, setSpaceOpen] = useState(false);
  const [spaceFiles, setSpaceFiles] = useState<StorageFile[]>([]);
  const [spaceParents, setSpaceParents] = useState<string[]>([]);
  const [spaceError, setSpaceError] = useState("");
  const [spaceLoading, setSpaceLoading] = useState(false);
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [pasteNotice, setPasteNotice] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadQueue = useRef(new UploadQueue(2));
  const pickerGeneration = useRef(0);
  const attachmentsRef = useRef<ComposerAttachment[]>([]);
  const attachmentsReady = attachments.every((attachment) => attachment.status === "ready" && attachment.ownerSessionId === sessionId);
  const readyAttachments = attachments.filter((attachment) => attachment.status === "ready" && attachment.ownerSessionId === sessionId);
  const canUploadAttachments = !disabled && !running && Boolean(sessionId || onEnsureSession);

  useEffect(() => {
    attachmentsRef.current = attachments;
  }, [attachments]);
  useEffect(() => () => {
    attachmentsRef.current.forEach((attachment) => {
      attachment.abort.abort();
      if (attachment.url.startsWith("blob:") && typeof URL.revokeObjectURL === "function") {
        URL.revokeObjectURL(attachment.url);
      }
    });
  }, []);
  useEffect(() => {
    if (canUploadAttachments) setPasteNotice("");
  }, [canUploadAttachments]);

  const sendValue = (value: string) => {
    const trimmed = value.trim();
    if ((!trimmed && readyAttachments.length === 0) || !attachmentsReady || disabled || sendDisabled || running) return;
    setContent("");
    if (readyAttachments.length > 0) {
      onSend(trimmed, readyAttachments);
    } else {
      onSend(trimmed);
    }
    attachmentsRef.current = [];
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
    attachment.abort = new AbortController();
    setAttachments((current) => current.map((item) => item.clientId === attachment.clientId
      ? { ...item, abort: attachment.abort, status: "uploading", errorMessage: undefined }
      : item));
    try {
      const uploadSessionId = attachment.ownerSessionId ?? sessionId ?? await onEnsureSession?.();
      if (!uploadSessionId) throw new Error("A conversation is required before uploading an attachment");
      setAttachments((current) => current.map((item) => item.clientId === attachment.clientId
        ? { ...item, ownerSessionId: uploadSessionId }
        : item));
      const image = attachment.sourceFile.type.startsWith("image/");
      if (!image) {
        const file = await uploadQueue.current.run(() => api.uploadStorageFile(attachment.sourceFile, workspaceId, undefined, attachment.clientId, attachment.abort.signal), attachment.abort.signal);
        setAttachments(current => current.map(item => item.clientId === attachment.clientId ? {
          ...item, fileId: file.id, expectedHash: file.sha256, fileName: file.name, workspaceId,
          url: fileSpaceViewUrl(file.id, workspaceId, file.sha256),
          mediaType: file.mime_type ?? "application/octet-stream", status: "ready", errorMessage: undefined,
        } : item));
        URL.revokeObjectURL(attachment.url);
        return;
      }
      const response = await uploadQueue.current.run(() => uploadAttachment(uploadSessionId, attachment.sourceFile), attachment.abort.signal);
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
    const removed = attachmentsRef.current.find(item => item.clientId === clientId);
    removed?.abort.abort();
    if (removed?.url.startsWith("blob:") && typeof URL.revokeObjectURL === "function") {
      URL.revokeObjectURL(removed.url);
    }
    attachmentsRef.current = attachmentsRef.current.filter(item => item.clientId !== clientId);
    setAttachments(current => current.filter(item => item.clientId !== clientId));
  };
  const hasAttachmentSpace = (notify: (message: string) => void) => {
    if (attachmentsRef.current.length < MAX_ATTACHMENTS) return true;
    notify(ATTACHMENT_LIMIT_NOTICE);
    return false;
  };
  const appendAttachment = (attachment: ComposerAttachment) => {
    attachmentsRef.current = [...attachmentsRef.current, attachment];
    setAttachments(current => [...current, attachment]);
    setPasteNotice("");
  };
  const addAndUploadFile = (file: File, displayName = file.name || "image") => {
    if (!canUploadAttachments || !hasAttachmentSpace(setPasteNotice)) return;
    if (!file.type.startsWith("image/") && !/^(readme|dockerfile|makefile)$/i.test(file.name) && !/\.(pdf|docx|xlsx|txt|text|md|markdown|mdown|mkd|csv|tsv|json|jsonl|py|js|mjs|cjs|ts|tsx|jsx|yaml|yml|xml|html|htm|css|scss|sql|log|java|c|cc|h|cpp|hpp|cs|go|rs|rb|php|swift|kt|vue|svelte|toml|ini|env|sh|bash|zsh|png|jpe?g|webp)$/i.test(file.name)) {
      setPasteNotice("暂不支持该附件格式"); return;
    }
    const newAttachment: ComposerAttachment = {
      clientId: createUuid(),
      abort: new AbortController(),
      sourceFile: file,
      ownerSessionId: sessionId ?? null,
      fileName: "",
      displayName,
      url: URL.createObjectURL(file),
      mediaType: file.type,
      width: 0,
      height: 0,
      status: "uploading",
    };

    appendAttachment(newAttachment);
    void upload(newAttachment);
  };
  const handleFileSelect = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    files.forEach(file => addAndUploadFile(file));
  };
  const openSpace = async (parents: string[] = []) => {
    const generation = ++pickerGeneration.current;
    setSpaceOpen(true);
    setSpaceParents(parents);
    setSpaceError("");
    setSpaceFiles([]);
    setSpaceLoading(true);
    try { const value = await api.listStorageFiles(workspaceId, parents.at(-1)); if (generation === pickerGeneration.current) setSpaceFiles(value.items); }
    catch (reason) { if (generation === pickerGeneration.current) setSpaceError(reason instanceof Error ? reason.message : "无法读取文件空间"); }
    finally { if (generation === pickerGeneration.current) setSpaceLoading(false); }
  };
  const attachSpaceFile = async (file: StorageFile) => {
    const notify = (message: string) => {
      setPasteNotice(message);
      if (spaceOpen) setSpaceError(message);
    };
    if (!canUploadAttachments || !hasAttachmentSpace(notify)) return;
    let owner: string | null | undefined;
    try {
      owner = sessionId ?? await onEnsureSession?.();
    } catch {
      notify("创建对话失败，请重试添加附件。");
      return;
    }
    // Multiple selections can wait on the same new conversation.
    if (!owner || !hasAttachmentSpace(notify)) return;
    const attachment: ComposerAttachment = {
      clientId: createUuid(), abort: new AbortController(), fileId: file.id, expectedHash: file.sha256, workspaceId,
      fileName: file.name, displayName: file.name, mediaType: file.mime_type ?? "application/octet-stream",
      url: fileSpaceViewUrl(file.id, workspaceId, file.sha256),
      width: 0, height: 0, status: "ready", ownerSessionId: owner,
      sourceFile: new File([], file.name), sizeBytes: file.size_bytes,
    };
    appendAttachment(attachment);
    setSpaceOpen(false);
  };
  const externalHandlers = useRef({ local: addAndUploadFile, remote: attachSpaceFile });
  useEffect(() => { externalHandlers.current = { local: addAndUploadFile, remote: attachSpaceFile }; });
  useEffect(() => {
    const local = (event: Event) => {
      const detail = (event as CustomEvent<{ file: File }>).detail;
      if (detail?.file instanceof File) externalHandlers.current.local(detail.file);
    };
    const remote = (event: Event) => {
      const detail = (event as CustomEvent<{ file: StorageFile; workspaceId?: string }>).detail;
      if (detail?.workspaceId === workspaceId && detail.file?.kind === "file") void externalHandlers.current.remote(detail.file);
    };
    window.addEventListener("file-space:attach-local", local);
    window.addEventListener("file-space:attach", remote);
    return () => { window.removeEventListener("file-space:attach-local", local); window.removeEventListener("file-space:attach", remote); };
  }, [workspaceId]);
  const handlePaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const itemImages = Array.from(event.clipboardData.items)
      .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
      .map((item) => item.getAsFile())
      .filter((file): file is File => file !== null);
    const images = itemImages.length > 0
      ? itemImages
      : Array.from(event.clipboardData.files).filter((file) => file.type.startsWith("image/"));
    const files = images.length > 0 ? images : Array.from(event.clipboardData.files);
    if (files.length === 0) return;

    // An image clipboard can also expose text/html or text/plain fallbacks.
    // Treat the image as the user's intent and avoid inserting those fallbacks.
    event.preventDefault();
    if (!canUploadAttachments) {
      setPasteNotice(running
        ? "当前正在生成，暂不能上传图片"
        : disabled
          ? "当前处于离线状态，暂不能上传图片"
          : "请先登录后再上传图片");
      return;
    }
    files.forEach((file, index) => addAndUploadFile(file, file.type.startsWith("image/") ? pastedImageName(file, index) : file.name));
  };

  return <div className={`composer-wrap ${centered ? "centered" : ""}`}
    onDragOver={event => { if (canUploadAttachments) event.preventDefault(); }}
    onDrop={event => { event.preventDefault(); if (canUploadAttachments) Array.from(event.dataTransfer.files).forEach(file => addAndUploadFile(file)); }}>
    <div className="quick-prompts">{prompts.map((prompt) => <button key={prompt} type="button" onClick={() => submitPrompt(prompt)} disabled={disabled || sendDisabled || running || !attachmentsReady}>{prompt}</button>)}</div>
    <div className="composer">
      {attachments.length > 0 && (
        <div className="composer-attachments" role="list" aria-label="已添加附件">
          {attachments.map(att => <AttachmentCard key={att.clientId} name={att.displayName ?? att.fileName}
            mediaType={att.mediaType} url={att.url} size={att.sizeBytes ?? att.sourceFile.size} status={att.status} error={att.errorMessage}
            onRemove={() => removeAttachment(att.clientId)} onRetry={() => void upload(att)} />)}
        </div>
      )}
      {pasteNotice && <p className="composer-upload-notice" role="alert">{pasteNotice}</p>}
      {selectionNotice && <p className="composer-upload-notice" role="status">{selectionNotice}</p>}
      <textarea
        value={content}
        onChange={(event) => {
          setContent(event.target.value);
          if (!sessionId && event.target.value.trim() && onEnsureSession) {
            void onEnsureSession();
          }
        }}
        onFocus={() => {
          if (!sessionId && onEnsureSession) {
            void onEnsureSession();
          }
        }}
        onKeyDown={keyDown}
        onPaste={handlePaste}
        disabled={disabled}
        rows={centered ? 3 : 1}
        placeholder="问一个 NLP 问题……"
        aria-label="学习问题"
      />
      <FileSpacePicker open={spaceOpen} files={spaceFiles} loading={spaceLoading} error={spaceError} nested={spaceParents.length > 0}
        onClose={() => { ++pickerGeneration.current; setSpaceOpen(false); }}
        onBack={() => void openSpace(spaceParents.slice(0, -1))}
        onChoose={file => file.kind === "folder" ? void openSpace([...spaceParents, file.id]) : void attachSpaceFile(file)} />
      <div className="composer-toolbar">
        <input type="file" ref={fileInputRef} hidden multiple accept="image/jpeg,image/png,image/webp,text/plain,.pdf,.docx,.xlsx,.txt,.text,.md,.markdown,.mdown,.mkd,.csv,.tsv,.json,.jsonl,.py,.js,.mjs,.cjs,.ts,.tsx,.jsx,.yaml,.yml,.xml,.html,.htm,.css,.scss,.sql,.log,.java,.c,.cc,.h,.cpp,.hpp,.cs,.go,.rs,.rb,.php,.swift,.kt,.vue,.svelte,.toml,.ini,.env,.sh,.bash,.zsh" onChange={handleFileSelect} />
        <AttachmentMenu disabled={!canUploadAttachments} onUpload={() => fileInputRef.current?.click()} onChooseFile={() => void openSpace()} />
        <span className="composer-brand"><GraduationCap size={15} />Nova · {STUDENT_AGENT_LABEL}</span>
        {thinkingControl}
        {contextControl}
        {running ? <button className="send-button stop" type="button" onClick={onCancel} disabled={cancelling} aria-label={cancelling ? "停止中" : "停止生成"}><Square size={14} fill="currentColor" /></button> : <button className="send-button" type="button" onClick={submit} disabled={disabled || !attachmentsReady || (!content.trim() && readyAttachments.length === 0)} aria-label="发送"><ArrowUp size={18} /></button>}
      </div>
    </div>
    <p className="composer-hint">Nova 也可能犯错，重要结论请结合教材验证</p>
  </div>;
}
