import { BookOpenText, Check, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

export interface TextInputDialogProps {
  open: boolean;
  title: string;
  description: string;
  label: string;
  initialValue?: string;
  placeholder?: string;
  confirmLabel?: string;
  maxLength?: number;
  onClose: () => void;
  onConfirm: (value: string) => void;
}

/** A focused, reusable text dialog for short names edited in the app. */
export function TextInputDialog({ open, title, description, label, initialValue = "", placeholder, confirmLabel = "保存修改", maxLength = 80, onClose, onConfirm }: TextInputDialogProps) {
  const [value, setValue] = useState(initialValue);
  const input = useRef<HTMLInputElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const inputId = useId();
  const close = () => onClose();

  useEffect(() => {
    if (!open) return;
    queueMicrotask(() => input.current?.focus());
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose, open]);

  if (!open) return null;
  const trimmedValue = value.trim();
  const submit = () => {
    if (!trimmedValue) return;
    onConfirm(trimmedValue);
    close();
  };

  return <div className="dialog-backdrop category-dialog-backdrop" role="presentation" onMouseDown={close}>
    <section className="category-dialog teacher-book-input-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} onMouseDown={(event) => event.stopPropagation()}>
      <header><span className="category-dialog-icon" aria-hidden="true"><BookOpenText size={20} /></span><div><h2 id={titleId}>{title}</h2><p id={descriptionId}>{description}</p></div><button className="category-dialog-close" type="button" aria-label={`关闭${title}`} onClick={close}><X size={17} /></button></header>
      <form onSubmit={(event) => { event.preventDefault(); submit(); }}><label htmlFor={inputId}>{label}</label><input ref={input} id={inputId} value={value} maxLength={maxLength} onChange={(event) => setValue(event.target.value)} placeholder={placeholder} autoComplete="off" /><small>{trimmedValue ? `${value.length}/${maxLength}` : `最多 ${maxLength} 个字符`}</small><footer><button className="category-dialog-cancel" type="button" onClick={close}>取消</button><button className="category-dialog-confirm" type="submit" disabled={!trimmedValue}><Check size={16} />{confirmLabel}</button></footer></form>
    </section>
  </div>;
}
