import { useEffect, useRef, useState } from "react";

import { api } from "@/platform/http/api";
import { TextInputDialog } from "@/shared/ui/TextInputDialog";
import type { WhiteboardLibraryItem } from "@/shared/types";
import { formatWhiteboardDeleteError } from "./whiteboardLibraryMessages";
import { WhiteboardAssetThumbnail } from "./WhiteboardAssetThumbnail";

const PREVIEW_WIDTH = 280;
const PREVIEW_HEIGHT = 170;
const PREVIEW_GAP = 8;
const PREVIEW_MARGIN = 12;

interface PreviewState {
  id: string;
  top: number;
  left: number;
}

function previewPosition(row: HTMLElement): { top: number; left: number } {
  const rect = row.getBoundingClientRect();
  const viewportWidth = window.innerWidth || PREVIEW_WIDTH + PREVIEW_MARGIN * 2;
  const viewportHeight = window.innerHeight || PREVIEW_HEIGHT + PREVIEW_MARGIN * 2;
  const left = Math.min(
    Math.max(PREVIEW_MARGIN, rect.left),
    Math.max(PREVIEW_MARGIN, viewportWidth - PREVIEW_WIDTH - PREVIEW_MARGIN),
  );
  const below = rect.bottom + PREVIEW_GAP;
  const above = rect.top - PREVIEW_HEIGHT - PREVIEW_GAP;
  const top = below + PREVIEW_HEIGHT <= viewportHeight - PREVIEW_MARGIN
    ? below
    : Math.max(PREVIEW_MARGIN, above);
  return { top, left };
}

export function WhiteboardLibraryManager({ items, onItemsChange }: {
  items: WhiteboardLibraryItem[];
  onItemsChange: (items: WhiteboardLibraryItem[]) => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [renameTarget, setRenameTarget] = useState<WhiteboardLibraryItem | null>(null);
  const rowRefs = useRef(new Map<string, HTMLLIElement>());

  useEffect(() => {
    if (!preview) return undefined;
    const updatePosition = () => {
      const row = rowRefs.current.get(preview.id);
      if (!row) {
        setPreview(null);
        return;
      }
      const position = previewPosition(row);
      setPreview((current) => current?.id === preview.id ? { id: preview.id, ...position } : current);
    };
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    updatePosition();
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [items, preview?.id]);

  const rename = async (item: WhiteboardLibraryItem, nextName: string) => {
    const trimmedName = nextName.trim();
    if (!trimmedName || trimmedName === item.name) return;
    if (typeof api.renameWhiteboardLibraryItem !== "function") {
      setError("当前版本暂不支持重命名素材。");
      return;
    }
    setBusyId(item.id);
    setError(null);
    try {
      const result = await api.renameWhiteboardLibraryItem(item.id, trimmedName);
      onItemsChange(items.map((candidate) => candidate.id === item.id ? result.item : candidate));
    } catch (error) {
      setError(error instanceof Error && error.message ? error.message : "素材重命名失败，请稍后重试。");
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (item: WhiteboardLibraryItem) => {
    if (!window.confirm(`确定删除白板素材“${item.name || "白板图画"}”吗？如果仍被教材引用，系统会阻止删除。`)) return;
    if (typeof api.deleteWhiteboardLibraryItem !== "function") {
      setError("当前版本暂不支持删除素材。");
      return;
    }
    setBusyId(item.id);
    setError(null);
    try {
      await api.deleteWhiteboardLibraryItem(item.id);
      onItemsChange(items.filter((candidate) => candidate.id !== item.id));
    } catch (error) {
      const detail = error instanceof Error && error.message ? error.message : "素材删除失败，请稍后重试。";
      const code = item.asset_code || item.id;
      setError(formatWhiteboardDeleteError(detail, code));
    } finally {
      setBusyId(null);
    }
  };

  return <section className={`teacher-whiteboard-library${expanded ? " is-expanded" : " is-collapsed"}`} aria-label="白板素材库管理">
    <div className="teacher-whiteboard-library-heading">
      <button
        type="button"
        className="teacher-whiteboard-library-toggle"
        aria-expanded={expanded}
        aria-controls="teacher-whiteboard-library-content"
        onClick={() => {
          setExpanded((current) => {
            if (current) setPreview(null);
            return !current;
          });
        }}
      >
        <span className="teacher-whiteboard-library-toggle-copy"><strong>白板素材库</strong><small>素材代号用于教材按钮和白板图画的精确对应。</small></span>
        <span className="teacher-whiteboard-library-toggle-state"><span>{items.length} 个素材</span><span aria-hidden="true">{expanded ? "收起 ↑" : "展开 ↓"}</span></span>
      </button>
    </div>
    {expanded && <div id="teacher-whiteboard-library-content">
      {error && <p className="teacher-whiteboard-library-error" role="alert">{error}</p>}
      {items.length === 0
        ? <p className="teacher-whiteboard-library-empty">暂无已命名素材。请在白板中将图画保存到素材库。</p>
        : <ul className="teacher-whiteboard-library-list">
          {items.map((item) => <li
            key={item.id}
            ref={(node) => {
              if (node) rowRefs.current.set(item.id, node);
              else rowRefs.current.delete(item.id);
            }}
            onMouseEnter={(event) => setPreview({ id: item.id, ...previewPosition(event.currentTarget) })}
            onMouseLeave={() => setPreview((current) => current?.id === item.id ? null : current)}
          >
            <div className="teacher-whiteboard-library-item-info">
              <strong>{item.name || "未命名图画"}</strong>
              <code title={item.id}>代号：{item.asset_code || item.id}</code>
            </div>
            <div className="teacher-whiteboard-library-item-actions">
              <button type="button" onClick={() => setRenameTarget(item)} disabled={busyId !== null} aria-label={`重命名 ${item.name || "白板图画"}`}>重命名</button>
              <button type="button" onClick={() => void remove(item)} disabled={busyId !== null} aria-label={`删除 ${item.name || "白板图画"}`}>删除</button>
            </div>
          </li>)}
        </ul>}
      {preview && (() => {
        const item = items.find((candidate) => candidate.id === preview.id);
        if (!item) return null;
        return <div className="teacher-whiteboard-library-preview" style={{ position: "fixed", top: preview.top, left: preview.left }}><WhiteboardAssetThumbnail elements={item.elements} label={item.name || "白板图画"} /></div>;
      })()}
    </div>}
    {renameTarget && <TextInputDialog
      open
      title="重命名白板素材"
      description="素材名称会同步显示在教材按钮和白板素材库中。"
      label="素材名称"
      initialValue={renameTarget.name || "白板图画"}
      placeholder="例如：Transformer 注意力结构"
      confirmLabel="保存修改"
      onClose={() => setRenameTarget(null)}
      onConfirm={(value) => {
        const target = renameTarget;
        setRenameTarget(null);
        void rename(target, value);
      }}
    />}
  </section>;
}
