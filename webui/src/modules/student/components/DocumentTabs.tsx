import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { WorkspaceTab } from "./WorkspaceTab";
import { filePresentation } from "./file-presentation";

export function DocumentTabs({ documents, activeId, prefix, onSelect, onClose, onReorder }: {
  documents: { id: string; name: string }[]; activeId: string | null; prefix: string;
  onSelect: (id: string) => void; onClose: (id: string) => void;
  onReorder: (source: string, target: string) => void;
}) {
  const dragged = useRef<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  useEffect(() => {
    if (activeId) document.getElementById(`${prefix}-tab-${activeId}`)?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [activeId, prefix]);
  const clear = () => { dragged.current = null; setOver(null); };
  const navigate = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const delta = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!delta && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? documents.length - 1 : (index + delta + documents.length) % documents.length;
    if (event.altKey && delta) { if (index + delta >= 0 && index + delta < documents.length) onReorder(documents[index].id, documents[next].id); }
    else { onSelect(documents[next].id); document.getElementById(`${prefix}-tab-${documents[next].id}`)?.focus(); }
  };
  return <div className="file-document-tabs tool-dock-tab-strip" role="tablist" aria-label="已打开的文件">
    {documents.map((item, index) => {
      const { Icon } = filePresentation(item.name);
      return <WorkspaceTab key={item.id} label={item.name} icon={<Icon size={14} />} selected={item.id === activeId}
        id={`${prefix}-tab-${item.id}`} panelId={`${prefix}-panel-${item.id}`} tabIndex={item.id === activeId ? 0 : -1}
        position={index + 1} count={documents.length} className={over === item.id ? "is-drag-over" : ""}
        onSelect={() => onSelect(item.id)} onClose={() => onClose(item.id)} onKeyDown={event => navigate(event, index)}
        onDragStart={event => { dragged.current = item.id; event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", item.id); }}
        onDragOver={event => { if (dragged.current && dragged.current !== item.id) { event.preventDefault(); event.dataTransfer.dropEffect = "move"; setOver(item.id); } }}
        onDrop={event => { if (dragged.current) { event.preventDefault(); onReorder(dragged.current, item.id); } clear(); }} onDragEnd={clear} />;
    })}
  </div>;
}
