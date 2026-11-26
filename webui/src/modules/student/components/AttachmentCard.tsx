import { LoaderCircle, RotateCcw, X } from "lucide-react";
import { filePresentation, fileSizeLabel } from "./file-presentation";

export function AttachmentCard({ name, mediaType, url, size, status, error, onRemove, onRetry }: {
  name: string; mediaType: string; url: string; size: number;
  status: "uploading" | "ready" | "error"; error?: string;
  onRemove: () => void; onRetry: () => void;
}) {
  const { kind, label, Icon } = filePresentation(name, mediaType);
  const body = <>
    <span className={`attachment-card-icon file-kind-${kind}`}>
      {kind === "image" ? <img src={url} alt={name} /> : <Icon size={21} aria-hidden="true" />}
    </span>
    <span className="attachment-card-details"><strong title={name}>{name}</strong>
      {status === "error" ? <span className="attachment-card-status error" role="status" title={error}>{error || "上传失败"}</span> : <span className="attachment-card-meta">{label} · {fileSizeLabel(size)}</span>}
    </span>
    {status === "uploading" && <LoaderCircle size={12} className="attachment-card-spinner attachment-card-uploading" aria-label="正在上传" />}
  </>;
  return <div className={`attachment-card ${status}`} role="listitem">
    {status === "ready" ? <a className="attachment-card-content" href={url} target="_blank" rel="noreferrer" aria-label={`预览附件 ${name}`}>{body}</a> : <div className="attachment-card-content">{body}</div>}
    <button className="attachment-card-remove" type="button" onClick={onRemove} aria-label={`移除附件 ${name}`} title="移除附件"><X size={13} /></button>
    {status === "error" && <button className="attachment-card-retry" type="button" onClick={onRetry} aria-label={`重试附件 ${name}`} title="重新上传"><RotateCcw size={13} /></button>}
  </div>;
}
