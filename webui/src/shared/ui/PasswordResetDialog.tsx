import { Check, KeyRound, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

export interface PasswordResetDialogProps {
  open: boolean;
  username: string;
  onClose: () => void;
  onConfirm: (password: string) => void;
}

/** Focused administrator dialog for replacing another user's password. */
export function PasswordResetDialog({ open, username, onClose, onConfirm }: PasswordResetDialogProps) {
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const firstInput = useRef<HTMLInputElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const passwordId = useId();
  const confirmationId = useId();

  useEffect(() => {
    if (!open) return;
    queueMicrotask(() => firstInput.current?.focus());
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose, open]);

  if (!open) return null;
  const valid = password.length >= 8 && password === confirmation;
  const mismatch = confirmation.length > 0 && password !== confirmation;
  const submit = () => {
    if (!valid) return;
    onConfirm(password);
    onClose();
  };

  return <div className="dialog-backdrop category-dialog-backdrop" role="presentation" onMouseDown={onClose}>
    <section className="category-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} onMouseDown={(event) => event.stopPropagation()}>
      <header>
        <span className="category-dialog-icon" aria-hidden="true"><KeyRound size={20} /></span>
        <div><h2 id={titleId}>重置用户密码</h2><p id={descriptionId}>为 @{username} 设置新的登录密码。</p></div>
        <button className="category-dialog-close" type="button" aria-label="关闭重置密码" onClick={onClose}><X size={17} /></button>
      </header>
      <form onSubmit={(event) => { event.preventDefault(); submit(); }}>
        <label htmlFor={passwordId}>新密码</label>
        <input ref={firstInput} id={passwordId} type="password" value={password} minLength={8} maxLength={128} onChange={(event) => setPassword(event.target.value)} placeholder="至少 8 位" autoComplete="new-password" />
        <label htmlFor={confirmationId}>确认新密码</label>
        <input id={confirmationId} type="password" value={confirmation} minLength={8} maxLength={128} onChange={(event) => setConfirmation(event.target.value)} placeholder="再次输入新密码" autoComplete="new-password" />
        <small>{mismatch ? "两次输入的密码不一致" : "密码长度为 8 至 128 位"}</small>
        <footer><button className="category-dialog-cancel" type="button" onClick={onClose}>取消</button><button className="category-dialog-confirm" type="submit" disabled={!valid}><Check size={16} />确认重置</button></footer>
      </form>
    </section>
  </div>;
}
