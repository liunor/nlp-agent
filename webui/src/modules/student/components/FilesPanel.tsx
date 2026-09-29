import { ArrowLeft, BookMarked, Download, FileCode2, FileText, FileUp, FolderOpen, FolderPlus, Pencil, RefreshCw, RotateCcw, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent, ChangeEvent } from "react";

import { api, storageFileDownloadUrl, type StorageFile, type StorageUsage, type StorageUsageBucket } from "@/platform/http/api";
import { createUuid } from "@/shared/utils/uuid";
import { DocumentCodeView } from "./DocumentCodeView";
import { MarkdownContent } from "./MarkdownContent";
import { importedFilesStorageKey, loadImportedFiles, type ImportedFile } from "./importedFiles";
// 800k UTF-16 characters is a generous but bounded preview. The byte slice
// below must stay a little larger than the worst case (4 bytes per character)
// so a CJK-only 800k-character document does not get cut short accidentally.
const MAX_PREVIEW_CHARS = 800_000;
const MAX_PREVIEW_BYTES = 4 * 1024 * 1024;

const CODE_EXTENSIONS: Record<string, string> = {
  js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "jsx", ts: "typescript", tsx: "tsx",
  py: "python", json: "json", css: "css", scss: "scss", html: "markup", htm: "markup", xml: "markup",
  yaml: "yaml", yml: "yaml", sh: "bash", bash: "bash", zsh: "bash", sql: "sql", java: "java",
  c: "c", h: "c", cpp: "cpp", hpp: "cpp", cs: "csharp", go: "go", rs: "rust", rb: "ruby",
  php: "php", swift: "swift", kt: "kotlin", vue: "markup", svelte: "markup", toml: "toml",
  ini: "ini", env: "bash",
};
// Extensionless files like Dockerfile / Makefile are matched by basename instead.
const CODE_FILENAMES: Record<string, string> = {
  dockerfile: "docker",
  makefile: "makefile",
};
const MARKDOWN_EXTENSIONS = new Set(["md", "markdown", "mdown", "mkd"]);
const TEXT_EXTENSIONS = new Set(["txt", "text", "log", "csv", "tsv"]);
const SUPPORTED_EXTENSIONS = new Set([
  ...MARKDOWN_EXTENSIONS,
  ...TEXT_EXTENSIONS,
  ...Object.keys(CODE_EXTENSIONS),
  "dockerfile",
  "makefile",
]);
const ACCEPT_ATTRIBUTE = Array.from(SUPPORTED_EXTENSIONS, (extension) => "." + extension).join(",");

export interface FilesPanelPreviewRequest {
  id: string;
  name: string;
  url: string;
  mediaType: string;
  bytes: number;
}

interface RemotePreviewState {
  request: FilesPanelPreviewRequest;
  status: "ready" | "error";
  file?: ImportedFile;
  error?: string;
}

function extensionOf(name: string) {
  const normalized = name.toLowerCase();
  const index = normalized.lastIndexOf(".");
  return index >= 0 ? normalized.slice(index + 1) : "";
}

function baseNameOf(name: string) {
  return name.toLowerCase().replace(/\.[^.]*$/, "");
}

function isSupportedFile(file: File) {
  const extension = extensionOf(file.name);
  if (extension) return SUPPORTED_EXTENSIONS.has(extension);
  if (CODE_FILENAMES[baseNameOf(file.name)]) return true;
  return file.type.startsWith("text/");
}

function describeFile(file: File): ImportedFile {
  const extension = extensionOf(file.name);
  const baseName = baseNameOf(file.name);
  const isMarkdown = MARKDOWN_EXTENSIONS.has(extension) || /readme(?:\.[\w-]+)?$/i.test(file.name);
  const codeLanguage = CODE_EXTENSIONS[extension] ?? CODE_FILENAMES[baseName] ?? "";
  const language = isMarkdown ? "markdown" : codeLanguage ? "code" : "text";
  return {
    id: createUuid(),
    name: file.name,
    content: "",
    language,
    codeLanguage,
    bytes: file.size,
    truncated: false,
    importedAt: Date.now(),
  };
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1024 / 1024).toFixed(1) + " MB";
}

function previewFile(file: File, text: string): ImportedFile {
  const meta = describeFile(file);
  return {
    ...meta,
    content: text.slice(0, MAX_PREVIEW_CHARS),
    truncated: file.size > MAX_PREVIEW_BYTES || text.length > MAX_PREVIEW_CHARS,
  };
}

function LearningImportPanel({ userId, workspaceId, previewRequest }: {
  userId: string | null;
  workspaceId: string;
  previewRequest?: FilesPanelPreviewRequest | null;
}) {
  const key = importedFilesStorageKey(userId, workspaceId);
  const [files, setFiles] = useState<ImportedFile[]>(() => loadImportedFiles(key));
  const [selectedId, setSelectedId] = useState<string | null>(files[files.length - 1]?.id ?? null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState("");
  const [remoteState, setRemoteState] = useState<RemotePreviewState | null>(null);
  const [localOverrideRequest, setLocalOverrideRequest] = useState<FilesPanelPreviewRequest | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const filesRef = useRef<ImportedFile[]>(files);
  const dragDepth = useRef(0);
  const remoteMatches = Boolean(previewRequest && remoteState?.request === previewRequest);
  const remoteSuppressed = localOverrideRequest === previewRequest;
  const remoteActive = remoteMatches && !remoteSuppressed;
  const remotePreview = remoteActive && remoteState?.status === "ready" ? remoteState.file ?? null : null;
  const remoteError = remoteActive && remoteState?.status === "error" ? remoteState.error ?? "" : "";
  const remoteLoading = Boolean(previewRequest) && !remoteSuppressed && !remoteMatches;
  const selected = remotePreview ?? files.find((file) => file.id === selectedId) ?? files[0] ?? null;

  useEffect(() => {
    if (!previewRequest) return undefined;
    const request = previewRequest;
    const controller = new AbortController();
    let active = true;
    void fetch(request.url, { credentials: "include", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const content = await response.text();
        if (!active) return;
        const source = new File([content], request.name, { type: request.mediaType });
        setRemoteState({
          request,
          status: "ready",
          file: { ...previewFile(source, content), id: `book-file:${request.id}`, bytes: request.bytes },
        });
      })
      .catch((reason: unknown) => {
        const aborted = reason && typeof reason === "object" && "name" in reason && reason.name === "AbortError";
        if (!active || aborted) return;
        setRemoteState({ request, status: "error", error: "教材文件预览加载失败，请稍后重试。" });
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [previewRequest]);

  const persistFiles = (next: ImportedFile[]) => {
    try {
      localStorage.setItem(key, JSON.stringify(next));
    } catch {
      setError("浏览器存储空间不足，新导入的文件可能无法在刷新后保留。");
    }
  };
  const commitFiles = (next: ImportedFile[]) => {
    filesRef.current = next;
    setFiles(next);
    persistFiles(next);
  };

  const importFiles = async (incoming: FileList | File[]) => {
    setError("");
    setLocalOverrideRequest(previewRequest ?? null);
    const list = Array.from(incoming).filter((item) => item && item.size > 0);
    if (!list.length) return;
    const importedFiles: ImportedFile[] = [];
    const problems: string[] = [];
    for (const file of list) {
      try {
        if (!isSupportedFile(file)) {
          problems.push(file.name + "：不支持的格式");
          continue;
        }
        // Always read a bounded prefix instead of the whole file. Large Markdown,
        // source dumps, or log files stay previewable without risking memory.
        const text = await file.slice(0, MAX_PREVIEW_BYTES).text();
        importedFiles.push(previewFile(file, text));
      } catch (reason) {
        problems.push(file.name + "：" + (reason instanceof Error ? reason.message : "无法读取"));
      }
    }
    if (problems.length) setError(problems.join("；"));
    if (!importedFiles.length) return;
    commitFiles([...filesRef.current, ...importedFiles]);
    // The list sorts newest-first, so select the last file of the batch to keep
    // the preview aligned with the top of the list.
    setSelectedId(importedFiles[importedFiles.length - 1].id);
  };

  const removeFile = (id: string) => {
    const next = filesRef.current.filter((file) => file.id !== id);
    commitFiles(next);
    if (selectedId === id) setSelectedId(next[next.length - 1]?.id ?? null);
  };
  const clearAll = () => {
    commitFiles([]);
    setSelectedId(null);
    setError("");
  };

  const onDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    void importFiles(event.dataTransfer.files);
  };
  const onInputChange = (event: ChangeEvent<HTMLInputElement>) => {
    void importFiles(event.target.files ?? []);
    event.target.value = "";
  };

  const selectLocalFile = (id: string) => {
    setLocalOverrideRequest(previewRequest ?? null);
    setSelectedId(id);
  };

  const selectedLanguage = selected?.language === "code" ? selected.codeLanguage : selected?.language ?? "text";
  const sortedFiles = useMemo(() => [...files].sort((a, b) => b.importedAt - a.importedAt), [files]);

  const hasRemoteState = remoteLoading || Boolean(remotePreview) || Boolean(remoteError);

  return (
    <section
      className={["files-panel", dragging && "dragging"].filter(Boolean).join(" ")}
      aria-label="文件工具"
      onDragEnter={(event) => { event.preventDefault(); dragDepth.current += 1; setDragging(true); }}
      onDragOver={(event) => { event.preventDefault(); }}
      onDragLeave={(event) => { event.preventDefault(); dragDepth.current = Math.max(0, dragDepth.current - 1); if (dragDepth.current === 0) setDragging(false); }}
      onDragEnd={() => { dragDepth.current = 0; setDragging(false); }}
      onDrop={onDrop}
    >
      <header className="files-panel-toolbar">
        <div><FolderOpen size={17} /><strong>文件</strong><small>导入 MD / TXT / 代码文档</small></div>
        <div className="files-panel-actions">
          <button type="button" onClick={() => inputRef.current?.click()}><FileUp size={15} />导入</button>
          {files.length > 0 && <button type="button" className="danger" onClick={clearAll}><Trash2 size={15} />清空</button>}
        </div>
        <input ref={inputRef} type="file" multiple accept={ACCEPT_ATTRIBUTE} aria-label="选择本地文件" onChange={onInputChange} />
      </header>

      {error && <div className="files-panel-error" role="alert">{error}<button type="button" aria-label="关闭提示" onClick={() => setError("")}><X size={14} /></button></div>}
      {remoteError && <div className="files-panel-error" role="alert">{remoteError}<button type="button" aria-label="关闭教材文件提示" onClick={() => setLocalOverrideRequest(previewRequest ?? null)}><X size={14} /></button></div>}

      {sortedFiles.length === 0 && !hasRemoteState ? (
        <button className="files-panel-dropzone" type="button" onClick={() => inputRef.current?.click()}>
          <span><FileUp size={22} /></span>
          <strong>导入学习文档</strong>
          <p>点击从电脑选择，或直接把 Markdown、TXT、代码文件拖到这里。</p>
        </button>
      ) : (
        <>
          {sortedFiles.length > 0 && <ul className="files-panel-list" aria-label="已导入文件">
            {sortedFiles.map((file) => {
              const Icon = file.language === "markdown" ? BookMarked : file.language === "code" ? FileCode2 : FileText;
              const active = !remotePreview && file.id === (selected?.id ?? sortedFiles[0].id);
              return (
                <li className={["files-panel-file", active && "active"].filter(Boolean).join(" ")} key={file.id}>
                  <button type="button" aria-label={"预览 " + file.name} aria-current={active ? "true" : undefined} onClick={() => selectLocalFile(file.id)}><Icon size={15} /><span><strong>{file.name}</strong><small>{formatBytes(file.bytes)}{file.truncated ? " · 仅预览前段" : ""}</small></span></button>
                  <button type="button" className="files-panel-remove" aria-label={"移除 " + file.name} onClick={() => removeFile(file.id)}><X size={14} /></button>
                </li>
              );
            })}
          </ul>}

          {remoteLoading ? <div className="files-panel-remote-state" role="status">正在打开教材文件……</div> : selected && <div className="files-panel-preview">
            {remotePreview && <div className="files-panel-external-banner"><BookMarked size={14} />来自知识教材</div>}
            <div className="files-panel-preview-header"><span>{selected.language === "markdown" ? <BookMarked size={15} /> : selected.language === "code" ? <FileCode2 size={15} /> : <FileText size={15} />}</span><strong>{selected.name}</strong>{selected && <small>{selected.language === "code" && selected.codeLanguage ? selected.codeLanguage : selected.language}</small>}</div>
            <div className="files-panel-preview-body">
              {selected.language === "markdown" ? <MarkdownContent>{selected.content}</MarkdownContent>
                : selected.language === "code" ? <DocumentCodeView language={selectedLanguage} code={selected.content} />
                  : <pre className="files-panel-plain">{selected.content}</pre>}
            </div>
          </div>}
        </>
      )}
    </section>
  );
}

function StorageMeter({ label, bucket }: { label: string; bucket: StorageUsageBucket }) {
  const percentage = Math.round(Math.min(1, Math.max(0, bucket.used_ratio)) * 100);
  return <div className={["storage-meter", bucket.state].join(" ")}>
    <div className="storage-meter-label"><strong>{label}</strong><span>{formatBytes(bucket.used_bytes)} / {formatBytes(bucket.quota_bytes)}</span></div>
    <div className="storage-meter-track" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percentage}><span className="storage-meter-fill" style={{ width: `${percentage}%` }} /></div>
  </div>;
}

function StorageManager({ workspaceId, onRefreshUsage }: { workspaceId?: string; onRefreshUsage: () => void }) {
  const [items, setItems] = useState<StorageFile[]>([]);
  const [folderPath, setFolderPath] = useState<StorageFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [newFolderName, setNewFolderName] = useState("");
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [trashMode, setTrashMode] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
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

  return <div className="storage-manager">
    <header className="storage-manager-toolbar">
      <div className="storage-breadcrumb" aria-label="当前文件夹路径">
        {trashMode ? <strong><Trash2 size={14} />回收站</strong> : <><button type="button" disabled={!folderPath.length} onClick={() => setFolderPath([])}><FolderOpen size={14} />我的文件</button>{folderPath.map((folder, index) => <span key={folder.id}><span>/</span><button type="button" onClick={() => setFolderPath(folderPath.slice(0, index + 1))}>{folder.name}</button></span>)}</>}
      </div>
      <div className="files-panel-actions">
        {!trashMode && folderPath.length > 0 && <button type="button" onClick={() => setFolderPath(folderPath.slice(0, -1))}><ArrowLeft size={14} />返回</button>}
        {!trashMode && <><button type="button" onClick={() => setNewFolderName((value) => value ? "" : "新建文件夹")}><FolderPlus size={14} />新建文件夹</button><button type="button" onClick={() => inputRef.current?.click()}><FileUp size={14} />上传</button></>}
        <button type="button" onClick={() => { setTrashMode((value) => !value); setFolderPath([]); setNewFolderName(""); }}><Trash2 size={14} />{trashMode ? "我的文件" : "回收站"}</button>
        <button type="button" aria-label="刷新文件列表" onClick={() => { refresh(); onRefreshUsage(); }}><RefreshCw size={14} /></button>
      </div>
      <input ref={inputRef} type="file" multiple onChange={uploadFiles} />
    </header>
    {!trashMode && newFolderName && <div className="storage-inline-form"><input autoFocus value={newFolderName} aria-label="新文件夹名称" onChange={(event) => setNewFolderName(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") createFolder(); if (event.key === "Escape") setNewFolderName(""); }} /><button type="button" onClick={createFolder}>创建</button><button type="button" onClick={() => setNewFolderName("")}>取消</button></div>}
    {error && <div className="files-panel-error" role="alert">{error}<button type="button" aria-label="关闭提示" onClick={() => setError("")}><X size={14} /></button></div>}
    {loading ? <div className="storage-empty">正在读取文件列表…</div> : items.length === 0 ? <div className="storage-empty"><FolderOpen size={24} /><strong>{trashMode ? "回收站为空" : "此文件夹为空"}</strong><span>{trashMode ? "删除的文件会在保留期后自动清理。" : "上传文件或新建文件夹，个人资料会和学习文档导入区分开。"}</span></div> : <div className="storage-list" role="list" aria-label={trashMode ? "回收站列表" : "个人文件列表"}>
      {items.map((item) => <div className="storage-row" key={item.id} role="listitem">
        <button type="button" className="storage-row-main" onClick={() => !trashMode && item.kind === "folder" && setFolderPath([...folderPath, item])}>
          {item.kind === "folder" ? <FolderOpen size={17} /> : <FileText size={17} />}<span><strong>{item.name}</strong><small>{item.kind === "folder" ? "文件夹" : formatBytes(item.size_bytes)}</small></span>
        </button>
        {trashMode ? <div className="storage-row-actions"><button type="button" aria-label={`恢复 ${item.name}`} onClick={() => restore(item)}><RotateCcw size={14} /></button><button type="button" className="danger" aria-label={`彻底删除 ${item.name}`} onClick={() => remove(item)}><Trash2 size={14} /></button></div> : renamingId === item.id ? <div className="storage-row-rename"><input autoFocus value={renameValue} aria-label={`重命名 ${item.name}`} onChange={(event) => setRenameValue(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") saveRename(item); if (event.key === "Escape") setRenamingId(null); }} /><button type="button" onClick={() => saveRename(item)}>保存</button></div> : <div className="storage-row-actions"><button type="button" aria-label={`重命名 ${item.name}`} onClick={() => beginRename(item)}><Pencil size={14} /></button>{item.kind === "file" && <a href={storageFileDownloadUrl(item.id, workspaceId)} aria-label={`下载 ${item.name}`} download><Download size={14} /></a>}<button type="button" className="danger" aria-label={`删除 ${item.name}`} onClick={() => remove(item)}><Trash2 size={14} /></button></div>}
      </div>)}
    </div>}
  </div>;
}

export function FilesPanel({ userId, workspaceId, previewRequest }: {
  userId?: string | null;
  workspaceId?: string;
  previewRequest?: FilesPanelPreviewRequest | null;
} = {}) {
  const [tab, setTab] = useState<"manager" | "import">("manager");
  const [usage, setUsage] = useState<StorageUsage | null>(null);
  const [usageError, setUsageError] = useState("");

  const refreshUsage = useCallback(() => {
    void api.getStorageUsage(workspaceId)
      .then((value) => { setUsage(value); setUsageError(""); })
      .catch((reason: unknown) => setUsageError(reason instanceof Error ? reason.message : "无法读取空间用量"));
  }, [workspaceId]);

  useEffect(() => { refreshUsage(); }, [refreshUsage]);

  return <section className="files-panel files-manager-shell" aria-label="文件工具">
    <div className="storage-summary">
      <div className="storage-summary-heading"><div><FolderOpen size={17} /><strong>文件空间</strong><small>{usage ? `${usage.role} · ${usage.files_count}/${usage.max_items} 个文件` : "正在读取空间用量…"}</small></div><button type="button" aria-label="刷新空间用量" onClick={refreshUsage}><RefreshCw size={14} /></button></div>
      {usage ? <><StorageMeter label="通用空间" bucket={usage.core} /><StorageMeter label="个人文件" bucket={usage.files} /></> : usageError ? <div className="storage-summary-error">{usageError}</div> : <div className="storage-meter-skeleton" />}
    </div>
    <div className="files-panel-tabs" role="tablist" aria-label="文件页面">
      <button type="button" role="tab" aria-selected={tab === "manager"} onClick={() => setTab("manager")}>我的文件</button>
      <button type="button" role="tab" aria-selected={tab === "import"} onClick={() => setTab("import")}>学习文档导入</button>
    </div>
    {tab === "manager" ? <StorageManager workspaceId={workspaceId} onRefreshUsage={refreshUsage} /> : <LearningImportPanel userId={userId ?? null} workspaceId={workspaceId ?? "default"} previewRequest={previewRequest} />}
  </section>;
}
