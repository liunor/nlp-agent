import * as Dialog from "@radix-ui/react-dialog";
import { ArrowLeft, ChevronRight, FileText, FolderOpen, X } from "lucide-react";
import type { StorageFile } from "@/platform/http/api";

export function FileSpacePicker({ open, files, loading, error, nested, onClose, onBack, onChoose }: {
  open: boolean;
  files: StorageFile[];
  loading: boolean;
  error: string;
  nested: boolean;
  onClose: () => void;
  onBack: () => void;
  onChoose: (file: StorageFile) => void;
}) {
  return <Dialog.Root open={open} onOpenChange={value => { if (!value) onClose(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="file-picker-overlay" />
      <Dialog.Content className="file-picker-dialog">
        <header className="file-picker-header">
          <Dialog.Title>从文件空间添加附件</Dialog.Title>
          <Dialog.Close className="file-picker-close" aria-label="关闭文件选择"><X size={18} /></Dialog.Close>
        </header>
        <Dialog.Description className="file-picker-description">选择文件添加到当前对话，不会复制原件。</Dialog.Description>
        {nested && <button type="button" className="file-picker-back" onClick={onBack}><ArrowLeft size={15} />上一级</button>}
        <div className="file-picker-list">
          {loading ? <p role="status">正在加载文件……</p> : error ? <p role="alert">{error}</p> : files.length === 0 ? <p>此文件夹暂无文件</p> : files.map(file =>
            <button key={file.id} type="button" className="file-picker-row" onClick={() => onChoose(file)}>
              {file.kind === "folder" ? <FolderOpen size={19} /> : <FileText size={19} />}
              <span>{file.name}</span>
              {file.kind === "folder" && <ChevronRight size={16} />}
            </button>)}
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
