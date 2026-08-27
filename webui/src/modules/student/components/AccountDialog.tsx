import * as Dialog from "@radix-ui/react-dialog";
import { LogOut, Settings, ShieldCheck, UserRound, X } from "lucide-react";

import type { AuthSession } from "@/shared/types";

export function AccountDialog({
  open,
  session,
  onClose,
  onLogout,
}: {
  open: boolean;
  session: AuthSession | null;
  onClose: () => void;
  onLogout: () => Promise<void>;
}) {
  const username = session?.username || session?.display_name || session?.user_id || "Nova 学习者";
  const displayName = session?.display_name || session?.username || "Nova 学习者";
  const roles = session?.roles?.join("、") || "student";
  return <Dialog.Root open={open} onOpenChange={(nextOpen) => { if (!nextOpen) onClose(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="account-dialog-overlay" />
      <Dialog.Content className="account-dialog-content" aria-describedby="account-dialog-description">
        <button className="login-dialog-close" type="button" onClick={onClose} aria-label="关闭账户管理"><X size={18} /></button>
        <div className="account-dialog-avatar"><UserRound size={27} /></div>
        <Dialog.Title>账户管理</Dialog.Title>
        <Dialog.Description id="account-dialog-description">当前会话由 Nova 的同源认证服务保护。</Dialog.Description>
        <dl>
          <div><dt>账号</dt><dd>{username}</dd></div>
          <div><dt>名称</dt><dd>{displayName}</dd></div>
          <div><dt>角色</dt><dd><ShieldCheck size={15} />{roles}</dd></div>
        </dl>
        <button className="account-dialog-profile" type="button" onClick={() => { onClose(); window.location.href = "/profile"; }}><Settings size={16} />个人设置</button>
        <button className="account-dialog-logout" type="button" onClick={() => void onLogout()}><LogOut size={16} />退出登录</button>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
