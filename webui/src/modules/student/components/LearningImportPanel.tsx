import { useCallback, useEffect, useId, useMemo, useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { BookMarked, FileCode2, FileText, FileUp, FolderOpen, Trash2, X } from "lucide-react";
import { importedFilesStorageKey, loadImportedFiles, type ImportedFile } from "./importedFiles";
import { ACCEPT_ATTRIBUTE, MAX_PREVIEW_BYTES, formatBytes, isSupportedFile, previewFile, type FilesPanelPreviewRequest } from "./document-import";
import { createUuid } from "@/shared/utils/uuid";
import { DocumentTabs } from "./DocumentTabs";
import { ImportedDocumentPreview, RemoteDocumentPreview } from "./DocumentPreview";
import { LocalFileReader } from "./LocalFileReader";

type OpenDocument = { id: string; name: string } & (
  { kind: "remote"; request: FilesPanelPreviewRequest } |
  { kind: "local"; file: File } | { kind: "text"; file: ImportedFile }
);

export function LearningImportPanel({ userId, workspaceId, previewRequest }: {
  userId: string | null; workspaceId: string; previewRequest?: FilesPanelPreviewRequest | null;
}) {
  const key = importedFilesStorageKey(userId, workspaceId);
  const [files, setFiles] = useState<ImportedFile[]>(() => loadImportedFiles(key));
  const [documents, setDocuments] = useState<OpenDocument[]>(() => files.length ? [{ id: files[files.length-1].id, name: files[files.length-1].name, kind: "text", file: files[files.length-1] }] : []);
  const [activeId, setActiveId] = useState<string | null>(() => files[files.length-1]?.id ?? null);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [pendingImports, setPendingImports] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const filesRef = useRef(files);
  const dragDepth = useRef(0);
  const importEpoch = useRef(0);
  // A clear or scope unmount invalidates every unfinished File.text() batch.
  useEffect(() => () => { importEpoch.current++; }, []);
  const prefix = useId();
  const openDocuments = useCallback((incoming: OpenDocument[]) => {
    setDocuments(current => {
      const next = [...current];
      for (const item of incoming) {
        const index = next.findIndex(doc => doc.id === item.id);
        if (index < 0) next.push(item); else next[index] = item;
      }
      return next;
    });
    if (incoming.length) setActiveId(incoming[incoming.length-1].id);
  }, []);
  // An incoming preview is an open-document command, not a replacement for other tabs.
  const [previousRequest, setPreviousRequest] = useState<FilesPanelPreviewRequest | null | undefined>(undefined);
  if (previousRequest !== previewRequest) {
    setPreviousRequest(previewRequest);
    if (previewRequest) openDocuments([{ id: `${previewRequest.source ?? "book"}:${previewRequest.workspaceId ?? workspaceId}:${previewRequest.id}`, name: previewRequest.name, kind: "remote", request: previewRequest }]);
  }
  const closeDocument = (id: string) => {
    const index = documents.findIndex(item => item.id === id);
    const next = documents.filter(item => item.id !== id);
    setDocuments(next);
    if (activeId === id) setActiveId(next[Math.min(index, next.length - 1)]?.id ?? null);
  };
  const reorder = (source: string, target: string) => setDocuments(current => {
    const from = current.findIndex(item => item.id === source), to = current.findIndex(item => item.id === target);
    if (from < 0 || to < 0 || from === to) return current;
    const next = [...current]; next.splice(to, 0, next.splice(from, 1)[0]); return next;
  });
  const commitFiles = (next: ImportedFile[]) => {
    filesRef.current = next; setFiles(next);
    try { localStorage.setItem(key, JSON.stringify(next)); }
    catch { setError("浏览器存储空间不足，新导入的文件可能无法在刷新后保留。"); }
  };
  const importFiles = async (incoming: FileList | File[]) => {
    const selected = Array.from(incoming).filter(file => file.size > 0);
    if (!selected.length) return;
    const generation = importEpoch.current;
    setError("");
    setPendingImports(count => count + 1);
    try {
      const imported: ImportedFile[] = [], opened: OpenDocument[] = [], problems: string[] = [];
      for (const file of selected) {
        try {
          if (/\.(pdf|docx)$/i.test(file.name)) {
            const id = createUuid(); opened.push({ id, name: file.name, kind: "local", file }); continue;
          }
          if (!isSupportedFile(file)) { problems.push(file.name + "：不支持的格式"); continue; }
          const content = await file.slice(0, MAX_PREVIEW_BYTES).text();
          if (generation !== importEpoch.current) return;
          const parsed = previewFile(file, content);
          imported.push(parsed); opened.push({ id: parsed.id, name: parsed.name, kind: "text", file: parsed });
        } catch (reason) { problems.push(file.name + "：" + (reason instanceof Error ? reason.message : "无法读取")); }
      }
      if (generation !== importEpoch.current) return;
      if (imported.length) commitFiles([...filesRef.current, ...imported]);
      if (problems.length) setError(problems.join("；"));
      openDocuments(opened);
    } finally {
      if (generation === importEpoch.current) setPendingImports(count => count - 1);
    }
  };
  const removeFile = (id: string) => { commitFiles(filesRef.current.filter(file => file.id !== id)); closeDocument(id); };
  const clearAll = () => {
    importEpoch.current++;
    setPendingImports(0);
    commitFiles([]); setDocuments(current => current.filter(doc => doc.kind === "remote"));
    setActiveId(documents.find(doc => doc.kind === "remote")?.id ?? null); setError("");
  };
  const onDrop = (event: DragEvent<HTMLElement>) => {
    if (!event.dataTransfer.files?.length) return;
    event.preventDefault(); dragDepth.current = 0; setDragging(false); void importFiles(event.dataTransfer.files);
  };
  const onInputChange = (event: ChangeEvent<HTMLInputElement>) => { void importFiles(event.target.files ?? []); event.target.value = ""; };
  const sortedFiles = useMemo(() => [...files].sort((a,b) => b.importedAt - a.importedAt), [files]);
  const canClearLocal = pendingImports > 0 || files.length > 0 || documents.some(doc => doc.kind === "local");
  return <section className={["files-panel file-document-workspace", dragging && "dragging"].filter(Boolean).join(" ")} aria-label="文件工具"
    onDragEnter={event => { if (event.dataTransfer?.types?.length && !event.dataTransfer.types.includes("Files")) return; event.preventDefault(); dragDepth.current++; setDragging(true); }}
    onDragOver={event => event.preventDefault()}
    onDragLeave={event => { event.preventDefault(); dragDepth.current = Math.max(0, dragDepth.current-1); if (!dragDepth.current) setDragging(false); }}
    onDragEnd={() => { dragDepth.current = 0; setDragging(false); }} onDrop={onDrop}>
    <header className="files-panel-toolbar"><div><FolderOpen size={17} /><strong>文档阅读</strong></div><div className="files-panel-actions"><button type="button" onClick={() => inputRef.current?.click()}><FileUp size={15} />导入</button>{canClearLocal && <button type="button" className="danger" onClick={clearAll}><Trash2 size={15} />清空</button>}</div><input ref={inputRef} type="file" multiple accept={ACCEPT_ATTRIBUTE} aria-label="选择本地文件" onChange={onInputChange} /></header>
    {error && <div className="files-panel-error" role="alert">{error}<button type="button" aria-label="关闭提示" onClick={() => setError("")}><X size={14} /></button></div>}
    {sortedFiles.length > 0 && <ul className="files-panel-list" aria-label="已导入文件">{sortedFiles.map(file => {
      const Icon = file.language === "markdown" ? BookMarked : file.language === "code" ? FileCode2 : FileText;
      return <li className={`files-panel-file ${activeId === file.id ? "active" : ""}`} key={file.id}>
        <button type="button" aria-label={`预览 ${file.name}`} aria-current={activeId === file.id ? "true" : undefined} onClick={() => openDocuments([{ id: file.id, name: file.name, kind: "text", file }])}><Icon size={15} /><span><strong>{file.name}</strong><small>{formatBytes(file.bytes)}{file.truncated ? " · 仅预览前段" : ""}</small></span></button>
        <button type="button" className="files-panel-remove" aria-label={`移除 ${file.name}`} onClick={() => removeFile(file.id)}><X size={14} /></button>
      </li>;
    })}</ul>}
    {documents.length > 0 ? <>
      <DocumentTabs documents={documents} activeId={activeId} prefix={prefix} onSelect={setActiveId} onClose={closeDocument} onReorder={reorder} />
      {documents.map(doc => <div key={doc.id} className="file-reader-pane" id={`${prefix}-panel-${doc.id}`} role="tabpanel" aria-labelledby={`${prefix}-tab-${doc.id}`} hidden={activeId !== doc.id}>
        {doc.kind === "remote" ? <RemoteDocumentPreview request={doc.request} onClose={() => closeDocument(doc.id)} /> : doc.kind === "local" ? <LocalFileReader file={doc.file} onClose={() => closeDocument(doc.id)} /> : <ImportedDocumentPreview file={doc.file} />}
      </div>)}
    </> : <button className="files-panel-dropzone" type="button" onClick={() => inputRef.current?.click()}><span><FileUp size={22} /></span><strong>导入学习文档</strong><p>选择或拖入文件</p></button>}
  </section>;
}
