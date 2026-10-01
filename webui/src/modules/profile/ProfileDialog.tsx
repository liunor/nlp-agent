import * as Dialog from "@radix-ui/react-dialog";
import { AlertTriangle, KeyRound, Settings, ShieldCheck, Trash2, UserRound, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/platform/http/api";
import type { UserProfile } from "@/shared/types";

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
}: {
  open: boolean;
  onClose: () => void;
  sessionRoles?: string[];
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
  const [activeSection, setActiveSection] = useState<"info" | "name" | "password" | "delete">("info");

  // ---------- 注销账号 ----------
  // 两步流程：Step 1 输入密码验证身份 → Step 2 弹出 10 秒倒计时警告窗口，倒计时结束后才能确认删除。
  const [deletePassword, setDeletePassword] = useState("");
  const [deleteVerifying, setDeleteVerifying] = useState(false);
  const [deleteVerified, setDeleteVerified] = useState(false);
  const [deleteErr, setDeleteErr] = useState("");
  const [deleteSubmitting, setDeleteSubmitting] = useState(false);
  const [deleteCountdown, setDeleteCountdown] = useState(10);
  // 连续验证失败计数：累计 5 次要求重新登录；密码验证成功后归零。
  const [deleteVerifyFails, setDeleteVerifyFails] = useState(0);

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

  // Step 1：输入密码，验证身份（仅校验密码，不会删除账号）
  const handleVerifyPassword = async () => {
    if (deleteVerifying || deleteSubmitting) return;
    const pwd = deletePassword;
    if (!pwd) { setDeleteErr("请输入登录密码"); return; }
    setDeleteVerifying(true);
    setDeleteErr("");
    try {
      await api.verifyPassword(pwd);
      // 密码正确：失败计数归零，进入倒计时确认阶段。
      setDeleteVerifyFails(0);
      setDeleteVerified(true);
      setDeleteCountdown(10);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        const nextFails = deleteVerifyFails + 1;
        setDeleteVerifyFails(nextFails);
        if (nextFails >= 5) {
          // 连续 5 次验证失败：注销当前会话并整页跳转到登录/注册页。
          // 整页导航会卸载设置弹窗，重新登录后走正常流程落到首页（不会回到注销验证页）。
          setDeleteErr("验证失败次数过多，正在返回登录页…");
          try {
            await api.logout();
          } catch {
            // 登出失败也继续跳转，登录页能处理已失效的会话。
          }
          window.location.href = "/login";
          return;
        }
        setDeleteErr("密码错误，请重新输入");
      } else {
        setDeleteErr(err instanceof ApiError ? err.message : "验证失败，请稍后重试");
      }
    } finally {
      setDeleteVerifying(false);
    }
  };

  // 倒计时：验证通过后开始 10 秒倒计时，期间禁止确认。
  useEffect(() => {
    if (!deleteVerified) return;
    if (deleteCountdown <= 0) return;
    const timer = setTimeout(() => setDeleteCountdown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [deleteVerified, deleteCountdown]);

  const handleFinalDelete = async () => {
    if (deleteCountdown > 0 || deleteSubmitting) return;
    setDeleteSubmitting(true);
    setDeleteErr("");
    try {
      await api.deleteAccount(deletePassword);
      // 跳回应用首页：未登录状态下 AuthGate(allowGuest) 会渲染应用外壳，
      // LoginDialog 依据 isAuthExpired 自动弹出，呈现与初次登录/注册一致的模糊背景效果。
      window.location.href = "/";
    } catch (err) {
      setDeleteErr(err instanceof ApiError ? err.message : "注销失败，请稍后重试");
      setDeleteSubmitting(false);
    }
  };

  const resetDeleteFlow = () => {
    setDeleteVerified(false);
    setDeleteCountdown(10);
    setDeletePassword("");
    setDeleteErr("");
    setDeleteSubmitting(false);
  };

  if (!open) return null;

  const roles = user?.roles ?? [];
  const roleLabels: Record<string, string> = { guest: "游客", student: "学生", teacher: "教师", developer: "开发者" };

  const sections: { id: typeof activeSection; label: string; icon: typeof UserRound }[] = [
    { id: "info", label: "基本信息", icon: UserRound },
    { id: "name", label: "修改昵称", icon: Settings },
    { id: "password", label: "修改密码", icon: KeyRound },
    { id: "delete", label: "注销账号", icon: Trash2 },
  ];

  return (
    <Dialog.Root open onOpenChange={(nextOpen) => { if (!nextOpen) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="profile-dialog-overlay" />
        <Dialog.Content className="profile-dialog-content" aria-describedby="profile-dialog-description">
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
                    onClick={() => { setActiveSection(id); setNameMsg(""); setNameErr(""); setPwdMsg(""); setPwdErr(""); setDeleteErr(""); if (id !== "delete") resetDeleteFlow(); }}
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

                {/* 注销账号 */}
                {activeSection === "delete" && (
                  <div className="profile-delete">
                    {/* 倒计时结束后仅保留最终警示横幅，隐藏顶部红条与说明段落，避免警示重复 */}
                    {!(deleteVerified && deleteCountdown === 0) && (
                      <>
                        <div className="profile-delete-warning">
                          <Trash2 size={16} />
                          <strong>注销账号不可撤销</strong>
                        </div>
                        <p className="profile-hint">
                          注销后，你的账号、密码、昵称、全部对话与学习记录、上传文件、反馈，
                          以及后端监控、账号管理和数据库中的相关数据都会被永久删除，且无法恢复。
                        </p>
                      </>
                    )}

                    {/* Step 1：验证身份 */}
                    {!deleteVerified ? (
                      <>
                        <label className="profile-label" htmlFor="profile-delete-password">
                          请输入登录密码以验证身份
                        </label>
                        <input
                          id="profile-delete-password"
                          type="password"
                          autoComplete="current-password"
                          value={deletePassword}
                          onChange={(e) => { setDeletePassword(e.target.value); setDeleteErr(""); }}
                          disabled={deleteVerifying || deleteSubmitting}
                          placeholder="登录密码"
                          maxLength={128}
                          className="profile-input"
                        />
                        {deleteErr && <p className="profile-msg-err">{deleteErr}</p>}
                        {deleteVerifyFails > 0 && !deleteVerified && (
                          <p className="profile-verify-fail-hint">
                            已失败 {deleteVerifyFails} 次，连续 5 次验证失败后将需要重新登录账号
                          </p>
                        )}
                        <button
                          type="button"
                          disabled={!deletePassword || deleteVerifying || deleteSubmitting}
                          className="profile-btn-danger"
                          onClick={() => void handleVerifyPassword()}
                        >
                          {deleteVerifying ? "验证中…" : "验证身份"}
                        </button>
                      </>
                    ) : (
                      /* Step 2：倒计时警告确认 */
                      <div className="profile-delete-countdown">
                        {deleteCountdown > 0 ? (
                          <div className="profile-delete-countdown-num" role="status" aria-live="polite">
                            {deleteCountdown}
                          </div>
                        ) : (
                          /* 倒计时结束：在原倒计时位置显示醒目警示 */
                          <p className="profile-delete-final-warning" role="alert">
                            <AlertTriangle size={20} aria-hidden="true" />
                            <span>
                              <strong>再次警告：</strong>此操作将彻底删除你的账号及所有数据，且<strong>不可恢复</strong>！
                            </span>
                          </p>
                        )}
                        {deleteErr && <p className="profile-msg-err">{deleteErr}</p>}
                        <div className="profile-delete-actions">
                          <button
                            type="button"
                            className="profile-btn-secondary"
                            onClick={resetDeleteFlow}
                            disabled={deleteSubmitting}
                          >
                            取消
                          </button>
                          <button
                            type="button"
                            disabled={deleteCountdown > 0 || deleteSubmitting}
                            className="profile-btn-danger"
                            onClick={() => void handleFinalDelete()}
                          >
                            {deleteSubmitting
                              ? "正在注销…"
                              : deleteCountdown > 0
                                ? `确认注销（${deleteCountdown}s）`
                                : "确认注销"}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )}

              </div>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
