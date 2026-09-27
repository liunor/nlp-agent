import { ArrowLeft, BookMarked, Download, FileCode2, FileText, FileUp, FolderOpen, FolderPlus, Pencil, RefreshCw, RotateCcw, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent, ChangeEvent } from "react";

import { api, storageFileDownloadUrl, type StorageFile, type StorageUsage, type StorageUsageBucket } from "@/platform/http/api";
import { createUuid } from "@/shared/utils/uuid";
import { DocumentCodeView } from "./DocumentCodeView";
import { MarkdownContent } from "./MarkdownContent";

const STORAGE_KEY = "nlp-agent.imported-files.v1";
const MAX_PREVIEW_CHARS = 800_000;
const MAX_FILE_BYTES = 3 * 1024 * 1024;

interface ImportedFile {
  id: string;
  name: string;
  content: string;
  language: "markdown" | "text" | "code";
  codeLanguage: string;
  bytes: number;
  truncated: boolean;
  importedAt: number;
}

const CODE_EXTENSIONS: Record<string, string> = {
  js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "jsx", ts: "typescript", tsx: "tsx",
  py: "python", json: "json", css: "css", scss: "scss", html: "markup", htm: "markup", xml: "markup",
  yaml: "yaml", yml: "yaml", sh: "bash", bash: "bash", zsh: "bash", sql: "sql", java: "java",
  c: "c", h: "c", cpp: "cpp", hpp: "cpp", cs: "csharp", go: "go", rs: "rust", rb: "ruby",
  php: "php", swift: "swift", kt: "kotlin", vue: "markup", svelte: "markup", toml: "toml",
  ini: "ini", env: "bash", dockerfile: "docker", makefile: "makefile",
};
const MARKDOWN_EXTENSIONS = new Set(["md", "markdown", "mdown", "mkd"]);

function extensionOf(name: string) {
  const normalized = name.toLowerCase();
  const index = normalized.lastIndexOf(".");
  return index >= 0 ? normalized.slice(index + 1) : "";
}

function describeFile(file: File): ImportedFile {
  const extension = extensionOf(file.name);
  const isMarkdown = MARKDOWN_EXTENSIONS.has(extension) || /readme(?:\.[\w-]+)?$/i.test(file.name);
  const codeLanguage = CODE_EXTENSIONS[extension] ?? "";
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

function loadImportedFiles(): ImportedFile[] {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]") as ImportedFile[];
    return Array.isArray(raw) ? raw.filter((item) => item && typeof item.name === "string" && typeof item.content === "string") : [];
  } catch {
    return [];
  }
}

function LearningImportPanel() {
  const [files, setFiles] = useState<ImportedFile[]>(() => loadImportedFiles());
  const [selectedId, setSelectedId] = useState<string | null>(files[files.length - 1]?.id ?? null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const filesRef = useRef<ImportedFile[]>(files);
  const selected = files.find((file) => file.id === selectedId) ?? files[0] ?? null;

  const persistFiles = (next: ImportedFile[]) => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      setError("浏览器存储空间不足，新导入的文件可能无法在刷新后保留。");
    }
  };
  const commitFiles = (next: ImportedFile[]) => {
    filesRef.current = next;
    setFiles(next);
    persistFiles(next);
  };

  const importText = (file: File, text: string): ImportedFile => {
    const meta = describeFile(file);
    return { ...meta, content: text.slice(0, MAX_PREVIEW_CHARS), truncated: text.length > MAX_PREVIEW_CHARS };
  };

  const importFiles = async (incoming: FileList | File[]) => {
    setError("");
    const list = Array.from(incoming).filter((item) => item && item.size > 0);
    if (!list.length) return;
    const nextFiles: ImportedFile[] = [];
    for (const file of list) {
      try {
        if (file.size > MAX_FILE_BYTES) {
          setError(file.name + " 超过 3 MB，暂不支持在线预览。");
          continue;
        }
        const text = await file.text();
        nextFiles.push(importText(file, text));
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "无法读取 " + file.name);
      }
    }
    if (!nextFiles.length) return;
    commitFiles([...filesRef.current, ...nextFiles]);
    setSelectedId(nextFiles[0].id);
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
    setDragging(false);
    void importFiles(event.dataTransfer.files);
  };
  const onInputChange = (event: ChangeEvent<HTMLInputElement>) => {
    void importFiles(event.target.files ?? []);
    event.target.value = "";
  };

  const selectedLanguage = selected?.language === "code" ? selected.codeLanguage : selected?.language ?? "text";
  const sortedFiles = useMemo(() => [...files].sort((a, b) => b.importedAt - a.importedAt), [files]);

  return (
    <section
      className={["files-panel", dragging && "dragging"].filter(Boolean).join(" ")}
      aria-label="文件工具"
      onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
      onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false); }}
      onDragEnd={() => setDragging(false)}
      onDrop={onDrop}
    >
      <header className="files-panel-toolbar">
        <div><FolderOpen size={17} /><strong>文件</strong><small>导入 MD / TXT / 代码文档</small></div>
        <div className="files-panel-actions">
          <button type="button" onClick={() => inputRef.current?.click()}><FileUp size={15} />导入</button>
          {files.length > 0 && <button type="button" className="danger" onClick={clearAll}><Trash2 size={15} />清空</button>}
        </div>
        <input ref={inputRef} type="file" multiple accept=".md,.markdown,.mdown,.mkd,.txt,.text,.log,.csv,.tsv,.js,.mjs,.cjs,.jsx,.ts,.tsx,.py,.json,.css,.scss,.html,.htm,.xml,.yaml,.yml,.sh,.bash,.zsh,.sql,.java,.c,.h,.cpp,.hpp,.cs,.go,.rs,.rb,.php,.swift,.kt,.vue,.svelte,.toml,.ini,.env,.dockerfile,.makefile" onChange={onInputChange} />
      </header>

      {error && <div className="files-panel-error" role="alert">{error}<button type="button" aria-label="关闭提示" onClick={() => setError("")}><X size={14} /></button></div>}

      {sortedFiles.length === 0 ? (
        <button className="files-panel-dropzone" type="button" onClick={() => inputRef.current?.click()}>
          <span><FileUp size={22} /></span>
          <strong>导入学习文档</strong>
          <p>点击从电脑选择，或直接把 Markdown、TXT、代码文件拖到这里。</p>
        </button>
      ) : (
        <>
          <div className="files-panel-list" role="listbox" aria-label="已导入文件">
            {sortedFiles.map((file) => {
              const Icon = file.language === "markdown" ? BookMarked : file.language === "code" ? FileCode2 : FileText;
              const active = file.id === (selected?.id ?? sortedFiles[0].id);
              return (
                <div className={["files-panel-file", active && "active"].filter(Boolean).join(" ")} key={file.id} role="option" aria-selected={active}>
                  <button type="button" onClick={() => setSelectedId(file.id)}><Icon size={15} /><span><strong>{file.name}</strong><small>{formatBytes(file.bytes)}{file.truncated ? " · 仅预览前段" : ""}</small></span></button>
                  <button type="button" className="files-panel-remove" aria-label={"移除 " + file.name} onClick={() => removeFile(file.id)}><X size={14} /></button>
                </div>
              );
            })}
          </div>

          <div className="files-panel-preview">
            <div className="files-panel-preview-header"><span>{selected?.language === "markdown" ? <BookMarked size={15} /> : selected?.language === "code" ? <FileCode2 size={15} /> : <FileText size={15} />}</span><strong>{selected?.name}</strong>{selected && <small>{selected.language === "code" && selected.codeLanguage ? selected.codeLanguage : selected.language}</small>}</div>
            <div className="files-panel-preview-body">
              {selected?.language === "markdown" ? <MarkdownContent>{selected.content}</MarkdownContent>
                : selected?.language === "code" ? <DocumentCodeView language={selectedLanguage} code={selected.content} />
                  : selected ? <pre className="files-panel-plain">{selected.content}</pre> : null}
            </div>
          </div>
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

  useEffect(() => { refresh(); }, [refresh]);

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

export function FilesPanel({ workspaceId }: { workspaceId?: string } = {}) {
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
    {tab === "manager" ? <StorageManager workspaceId={workspaceId} onRefreshUsage={refreshUsage} /> : <LearningImportPanel />}
  </section>;
}
