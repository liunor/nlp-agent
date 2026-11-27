import type { DragEventHandler, KeyboardEventHandler, ReactNode } from "react";
import { X } from "lucide-react";

/** Shared chrome for tool tabs and file-reader tabs. */
export function WorkspaceTab({ label, icon, selected, position, count, className = "", closeLabel, title, badge, id, panelId, tabIndex, onSelect, onClose, onKeyDown, ...drag }: {
  label: string; icon: ReactNode; selected: boolean; position: number; count: number;
  closeLabel?: string; title?: string; badge?: ReactNode; className?: string; id?: string; panelId?: string; tabIndex?: number;
  onSelect: () => void; onClose: () => void; onKeyDown?: KeyboardEventHandler<HTMLButtonElement>;
  onDragStart?: DragEventHandler<HTMLDivElement>; onDragOver?: DragEventHandler<HTMLDivElement>;
  onDrop?: DragEventHandler<HTMLDivElement>; onDragEnd?: DragEventHandler<HTMLDivElement>;
}) {
  return <div className={`tool-dock-tab ${selected ? "active" : ""} ${className}`} draggable {...drag}>
    <button type="button" id={id} role="tab" aria-controls={panelId} aria-selected={selected} aria-posinset={position} aria-setsize={count} tabIndex={tabIndex} title={title ?? label} onClick={onSelect} onKeyDown={onKeyDown}>{icon}<span>{label}</span>{badge}</button>
    <button type="button" aria-label={closeLabel ?? `关闭 ${label}`} title={`关闭 ${label}`} onClick={onClose}><X size={14} /></button>
  </div>;
}
