import * as Dialog from "@radix-ui/react-dialog";
import { Coins, KeyRound, Settings, ShieldCheck, UserRound, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/platform/http/api";
import type { UserProfile } from "@/shared/types";
import { QuotaUsagePage } from "@/modules/quota/QuotaUsagePage";

/**
 * 个人设置弹层 — Radix 标准 Dialog（role="dialog"、aria-modal、焦点陷阱、
 * Esc / 点击遮罩关闭均由 Radix 提供），毛玻璃风格与账户管理弹窗一致。
 *
 * 父组件仅在打开时挂载本组件（{profileOpen && <ProfileDialog …/>}），
 * 关闭即卸载，页签 / 输入内容 / 提示信息自然全部重置。
 */
export function ProfileDialog({
  open,
  onClose,
  sessionRoles,
  userId,
  workspaceIds,
}: {
  open: boolean;
  onClose: () => void;
  sessionRoles?: string[];
  userId?: string;
  workspaceIds?: string[];
}) {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(false);

  // ---------- 昵称 ----------
  const [displayName, setDisplayName] = useState("");
  const [nameMsg, setNameMsg] = useState("");
  const [nameErr, setNameErr] = useState("");
  const [nameSaving, setNameSaving] = useState(false);

  // ---------- 密码 ----------
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [pwdMsg, setPwdMsg] = useState("");
  const [pwdErr, setPwdErr] = useState("");
  const [pwdSaving, setPwdSaving] = useState(false);

  // ---------- active section ----------
  const [activeSection, setActiveSection] = useState<"info" | "name" | "password" | "quota">("info");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const u = await api.getCurrentUser();
      // /users/me 已填充角色；若响应异常为空，退回会话里的角色兜底，
      // 避免开发者 / 教师被误显示成"游客"。
      if ((!u.roles || u.roles.length === 0) && sessionRoles?.length) {
        u.roles = [...sessionRoles];
      }
      setUser(u);
      setDisplayName(u.display_name);
    } catch {
      // AuthGate handles unauthenticated
    } finally {
      setLoading(false);
    }
  }, [sessionRoles]);

  useEffect(() => {
    if (!open) return;
    queueMicrotask(() => void load());
  }, [open, load]);

  const handleNameSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setNameMsg("");
    setNameErr("");
    const trimmed = displayName.trim();
    if (!trimmed) { setNameErr("昵称不能为空"); return; }
    setNameSaving(true);
    try {
      const updated = await api.updateProfile({ display_name: trimmed });
      // PATCH /users/me 同样返回角色，保留兜底逻辑避免角色丢失。
      setUser({ ...updated, roles: updated.roles?.length ? updated.roles : user?.roles ?? [] });
      setNameMsg("昵称已更新");
    } catch (err) {
      setNameErr(err instanceof ApiError ? err.message : "更新失败");
    } finally {
      setNameSaving(false);
    }
  };

  const handlePwdSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPwdMsg("");
    setPwdErr("");
    if (newPassword.length < 8) { setPwdErr("新密码至少 8 位"); return; }
    if (newPassword !== confirmPassword) { setPwdErr("两次输入的密码不一致"); return; }
    setPwdSaving(true);
    try {
      await api.changePassword({ current_password: currentPassword, new_password: newPassword });
      setPwdMsg("密码修改成功，即将跳转登录…");
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setTimeout(() => { window.location.href = "/login"; }, 1500);
    } catch (err) {
      setPwdErr(err instanceof ApiError ? err.message : "修改失败");
    } finally {
      setPwdSaving(false);
    }
  };

  if (!open) return null;

  const roles = user?.roles ?? [];
  const roleLabels: Record<string, string> = { guest: "游客", student: "学生", teacher: "教师", developer: "开发者" };

  const sections: { id: typeof activeSection; label: string; icon: typeof UserRound }[] = [
    { id: "info", label: "基本信息", icon: UserRound },
    { id: "name", label: "修改昵称", icon: Settings },
    { id: "password", label: "修改密码", icon: KeyRound },
    { id: "quota", label: "额度与用量", icon: Coins },
  ];

  return (
    <Dialog.Root open onOpenChange={(nextOpen) => { if (!nextOpen) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="profile-dialog-overlay" />
        <Dialog.Content className={`profile-dialog-content${activeSection === "quota" ? " profile-dialog-content-wide" : ""}`} aria-describedby="profile-dialog-description">
          <button className="login-dialog-close" type="button" onClick={onClose} aria-label="关闭个人设置">
            <X size={18} />
          </button>

          {loading ? (
            <>
              <Dialog.Title className="profile-title">个人设置</Dialog.Title>
              <Dialog.Description id="profile-dialog-description" className="profile-subtitle" role="status">
                加载中…
              </Dialog.Description>
            </>
          ) : (
            <>
              {/* 头像 */}
              <div className="profile-avatar"><UserRound size={27} /></div>

              {/* 标题 */}
              <Dialog.Title className="profile-title">个人设置</Dialog.Title>
              <Dialog.Description id="profile-dialog-description" className="profile-subtitle">
                管理您的账户信息和密码
              </Dialog.Description>

              {/* 侧导航 */}
              <nav className="profile-nav">
                {sections.map(({ id, label, icon: Icon }) => (
                  <button
                    key={id}
                    type="button"
                    className={activeSection === id ? "active" : ""}
                    onClick={() => { setActiveSection(id); setNameMsg(""); setNameErr(""); setPwdMsg(""); setPwdErr(""); }}
                  >
                    <Icon size={15} />{label}
                  </button>
                ))}
              </nav>

              {/* 内容区 */}
              <div className="profile-content">

                {/* 基本信息 */}
                {activeSection === "info" && user && (
                  <dl className="profile-info-list">
                    <div><dt>账号</dt><dd>{user.username}</dd></div>
                    <div><dt>名称</dt><dd>{user.display_name}</dd></div>
                    <div><dt>角色</dt><dd><ShieldCheck size={14} />{roles.map(r => roleLabels[r] || r).join("、") || "游客"}</dd></div>
                    <div><dt>注册时间</dt><dd>{new Date(user.created_at).toLocaleString("zh-CN")}</dd></div>
                    <div><dt>上次更新</dt><dd>{new Date(user.updated_at).toLocaleString("zh-CN")}</dd></div>
                  </dl>
                )}

                {/* 修改昵称 */}
                {activeSection === "name" && (
                  <form className="profile-form" onSubmit={handleNameSubmit}>
                    <label className="profile-label" htmlFor="profile-name">新昵称</label>
                    <input
                      id="profile-name"
                      type="text"
                      value={displayName}
                      onChange={(e) => setDisplayName(e.target.value)}
                      disabled={nameSaving}
                      maxLength={128}
                      placeholder="1 ~ 128 个字符"
                      className="profile-input"
                    />
                    {nameMsg && <p className="profile-msg-ok">{nameMsg}</p>}
                    {nameErr && <p className="profile-msg-err">{nameErr}</p>}
                    <button type="submit" disabled={nameSaving} className="profile-btn-primary">
                      {nameSaving ? "保存中…" : "保存昵称"}
                    </button>
                  </form>
                )}

                {/* 修改密码 */}
                {activeSection === "password" && (
                  <form className="profile-form" onSubmit={handlePwdSubmit}>
                    <p className="profile-hint">修改密码后所有已登录设备将自动退出，需重新登录。</p>

                    <label className="profile-label" htmlFor="pwd-current">当前密码</label>
                    <input id="pwd-current" type="password" autoComplete="current-password"
                      value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)}
                      disabled={pwdSaving} className="profile-input" />

                    <label className="profile-label" htmlFor="pwd-new">新密码</label>
                    <input id="pwd-new" type="password" autoComplete="new-password"
                      value={newPassword} onChange={(e) => setNewPassword(e.target.value)}
                      disabled={pwdSaving} placeholder="至少 8 位" maxLength={128} className="profile-input" />

                    <label className="profile-label" htmlFor="pwd-confirm">确认新密码</label>
                    <input id="pwd-confirm" type="password" autoComplete="new-password"
                      value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)}
                      disabled={pwdSaving} placeholder="再次输入新密码" maxLength={128} className="profile-input" />

                    {pwdMsg && <p className="profile-msg-ok">{pwdMsg}</p>}
                    {pwdErr && <p className="profile-msg-err">{pwdErr}</p>}

                    <button type="submit" disabled={pwdSaving} className="profile-btn-primary">
                      {pwdSaving ? "修改中…" : "修改密码"}
                    </button>
                  </form>
                )}

                {activeSection === "quota" && <QuotaUsagePage embedded userId={userId} workspaceIds={workspaceIds} />}
              </div>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
