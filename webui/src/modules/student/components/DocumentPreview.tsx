import { useEffect, useState } from "react";
import { BookMarked, Download, X } from "lucide-react";
import { api, storageFileDownloadUrl } from "@/platform/http/api";
import type { ImportedFile } from "./importedFiles";
import { previewFile, MAX_PREVIEW_CHARS, MAX_PREVIEW_BYTES, type FilesPanelPreviewRequest } from "./document-import";
import { FileSpaceReader } from "./FileSpaceReader";
import { filePresentation } from "./file-presentation";
import { DocumentCodeView } from "./DocumentCodeView";
import { MarkdownContent } from "./MarkdownContent";

export function ImportedDocumentPreview({ file, children }: { file: ImportedFile; children?: React.ReactNode }) {
  const { Icon } = filePresentation(file.name);
  return <div className="files-panel-preview">
    <header className="files-panel-preview-header"><Icon size={16} /><strong>{file.name}</strong><small>{file.language === "code" ? file.codeLanguage : file.language}</small></header>
    <div className="files-panel-preview-body">{children}{file.bytes === 0 && !file.content ? <p className="files-panel-plain">此文件为空（0 B）。</p> : file.language === "markdown" ? <MarkdownContent>{file.content}</MarkdownContent> : file.language === "code" ? <DocumentCodeView language={file.codeLanguage} code={file.content} /> : <pre className="files-panel-plain">{file.content}</pre>}</div>
  </div>;
}

export function RemoteDocumentPreview({ request, onClose }: { request: FilesPanelPreviewRequest; onClose: () => void }) {
  const structured = request.source === "storage" && /\.(pdf|docx|xlsx|png|jpe?g|webp)$/i.test(request.name);
  const [fullReader, setFullReader] = useState(false);
  const [result, setResult] = useState<{ request: FilesPanelPreviewRequest; file?: ImportedFile; error?: string } | null>(null);
  const file = result?.request === request ? result.file : null;
  const error = result?.request === request ? result.error : "";
  useEffect(() => {
    if (structured) return;
    let active = true;
    const controller = new AbortController();
    const read = async () => {
      if (request.source === "storage") return api.readStorageFile(request.id, request.workspaceId);
      const response = await fetch(request.url, { credentials: "include", signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return { content: await response.text(), truncated: false, bytes_read: request.bytes };
    };
    void read().then(result => {
      if (!active) return;
      const source = new File([result.content], request.name, { type: request.mediaType });
      setResult({ request, file: { ...previewFile(source, result.content), bytes: result.truncated ? request.bytes : result.bytes_read,
        truncated: result.truncated || source.size > MAX_PREVIEW_BYTES || result.content.length > MAX_PREVIEW_CHARS } });
    }).catch((reason: unknown) => { if (active) setResult({ request, error: reason instanceof Error ? reason.message : "无法读取文件" }); });
    return () => { active = false; controller.abort(); };
  }, [request, structured]);
  if (structured || fullReader) return <FileSpaceReader fileId={request.id} name={request.name} workspaceId={request.workspaceId} />;
  if (error) return <div className="files-panel-error" role="alert">{error}
    {request.source === "storage" && <><button type="button" onClick={() => setFullReader(true)}>尝试分段读取</button><a href={storageFileDownloadUrl(request.id, request.workspaceId)} download><Download size={14} />下载文件</a></>}
    <button type="button" aria-label="关闭文件预览提示" onClick={onClose}><X size={14} /></button>
  </div>;
  if (!file) return <div className="files-panel-remote-state" role="status">正在打开{request.source === "storage" ? "文件" : "教材文件"}……</div>;
  return <>{request.source !== "storage" && <div className="files-panel-external-banner"><BookMarked size={14} />来自知识教材</div>}
    <ImportedDocumentPreview file={file}>{request.source === "storage" && file.truncated && <button type="button" onClick={() => setFullReader(true)}>分段读取完整文件</button>}</ImportedDocumentPreview>
  </>;
}
