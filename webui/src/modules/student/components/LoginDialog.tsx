import * as Dialog from "@radix-ui/react-dialog";
import { LockKeyhole, RefreshCw, X } from "lucide-react";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/platform/http/api";
import { ConsentNotice, LegalDocumentModal } from "@/modules/auth/LegalConsent";
import type { LegalDocumentKind } from "@/modules/auth/legalDocuments";

type Tab = "login" | "register";

interface LoginDialogProps {
  open: boolean;
  expired?: boolean;
  onClose: () => void;
  onAuthenticate: (username: string, password: string) => Promise<void>;
  notice?: string;
  onNoticeDismiss?: () => void;
}

export function LoginDialog({ open, expired = false, onClose, onAuthenticate, notice, onNoticeDismiss }: LoginDialogProps) {
  const [tab, setTab] = useState<Tab>("login");
  const [registrationNotice, setRegistrationNotice] = useState("");

  const close = useCallback(() => {
    setTab("login");
    onClose();
  }, [onClose]);
  const dismiss = useCallback(() => {
    if (expired) return;
    close();
  }, [close, expired]);
  const handleLoginSuccess = useCallback(() => {
    onNoticeDismiss?.();
    close();
  }, [close, onNoticeDismiss]);

  return (
    <Dialog.Root open={open} onOpenChange={(nextOpen: boolean) => { if (!nextOpen) dismiss(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="login-dialog-overlay" />
        <Dialog.Content className="login-dialog-content" aria-describedby="login-dialog-description">
          {!expired && (
  <button
    className="login-dialog-close"
    type="button"
    onClick={dismiss}
    aria-label="关闭"
  >
    <X size={18} />
  </button>
)}

          <Dialog.Description id="login-dialog-description">
            {tab === "login"
              ? "登录后可创建学习会话并使用实时对话功能。"
              : "使用邮箱注册新账户，开始您的学习之旅。"}
          </Dialog.Description>
          {expired && (
  <p className="login-dialog-error login-dialog-expired-message" role="alert">
    登录状态已失效，请重新登录后继续使用。
  </p>
)}

          {tab === "login" && (notice || registrationNotice) && <p className="login-dialog-notice" role="status">{notice || registrationNotice}</p>}
          {tab === "login" ? (
            <LoginForm onAuthenticate={onAuthenticate} onSubmitAttempt={() => setRegistrationNotice("")} onSuccess={handleLoginSuccess} onSwitchToRegister={() => { onNoticeDismiss?.(); setRegistrationNotice(""); setTab("register"); }} />
          ) : (
            <RegisterForm onSwitchToLogin={() => { onNoticeDismiss?.(); setRegistrationNotice(""); setTab("login"); }} onSuccess={() => { onNoticeDismiss?.(); setRegistrationNotice("注册成功，请使用手机号登录"); setTab("login"); }} />
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

// ---------------------------------------------------------------------------
// LoginForm
// ---------------------------------------------------------------------------

function LoginForm({
  onAuthenticate,
  onSubmitAttempt,
  onSuccess,
  onSwitchToRegister,
}: {
  onAuthenticate: (username: string, password: string) => Promise<void>;
  onSubmitAttempt: () => void;
  onSuccess: () => void;
  onSwitchToRegister: () => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [hasAgreed, setHasAgreed] = useState(false);
  const [legalDocument, setLegalDocument] = useState<LegalDocumentKind | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!username.trim() || !password || submitting) return;
    if (!hasAgreed) {
      setError("请先阅读并同意用户协议和隐私政策");
      return;
    }
    onSubmitAttempt();
    setSubmitting(true);
    setError("");
    try {
      await onAuthenticate(username.trim(), password);
      onSuccess();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "登录失败，请稍后重试。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={(event) => void submit(event)}>
      <Dialog.Title>登录 Nova</Dialog.Title>
      <label>
        <span>邮箱</span>
        <input
          autoComplete="username"
          autoFocus
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          disabled={submitting}
          placeholder="请输入邮箱"
          maxLength={254}
          required
        />
      </label>
      <label>
        <span>密码</span>
        <input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          disabled={submitting}
          maxLength={512}
          required
        />
      </label>
      {error && <p className="login-dialog-error" role="alert">{error}</p>}
      <button
        className="login-dialog-submit"
        type="submit"
        disabled={submitting || !username.trim() || !password || !hasAgreed}
      >
        <LockKeyhole size={16} />
        {submitting ? "正在验证" : "登录并继续"}
      </button>
      <ConsentNotice
        id="dialog-login-consent"
        checked={hasAgreed}
        disabled={submitting}
        onChange={(checked) => { setHasAgreed(checked); setError(""); }}
        onOpen={setLegalDocument}
      />
      <LegalDocumentModal document={legalDocument} onClose={() => setLegalDocument(null)} />
      <div style={{ marginTop: 12, textAlign: "center", fontSize: 13, color: "var(--text-secondary, #6b7280)" }}>
        <span>还没有账号？</span>{" "}
        <button
          type="button"
          onClick={onSwitchToRegister}
          style={{ background: "none", border: "none", color: "var(--accent, #3b82f6)", cursor: "pointer", fontWeight: 500, padding: 0 }}
        >
          立即注册
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// RegisterForm
// ---------------------------------------------------------------------------

function RegisterForm({
  onSwitchToLogin,
  onSuccess,
}: {
  onSwitchToLogin: () => void;
  onSuccess?: () => void;
}) {
  const [phone, setPhone] = useState("");
  const [smsCode, setSmsCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [smsSending, setSmsSending] = useState(false);
  const [smsCooldown, setSmsCooldown] = useState(0);

  // Separate captchas are used for SMS sending and account registration.
  const [smsCaptchaId, setSmsCaptchaId] = useState("");
  const [smsCaptchaImage, setSmsCaptchaImage] = useState("");
  const [smsCaptchaCode, setSmsCaptchaCode] = useState("");
  const [regCaptchaId, setRegCaptchaId] = useState("");
  const [regCaptchaImage, setRegCaptchaImage] = useState("");
  const [regCaptchaCode, setRegCaptchaCode] = useState("");
  const [smsSent, setSmsSent] = useState(false);
  const [hasAgreed, setHasAgreed] = useState(false);
  const [legalDocument, setLegalDocument] = useState<LegalDocumentKind | null>(null);
  const captchaRequestVersion = useRef({ sms: 0, reg: 0 });

  const loadCaptcha = useCallback(async (target: "sms" | "reg") => {
    const requestVersion = ++captchaRequestVersion.current[target];
    try {
      const resp = await api.getCaptcha();
      if (requestVersion !== captchaRequestVersion.current[target]) return true;
      if (target === "sms") {
        setSmsCaptchaId(resp.captcha_id);
        setSmsCaptchaImage(resp.image);
      } else {
        setRegCaptchaId(resp.captcha_id);
        setRegCaptchaImage(resp.image);
      }
      return true;
    } catch {
      if (requestVersion !== captchaRequestVersion.current[target]) return true;
      if (target === "sms") {
        setSmsCaptchaId("");
        setSmsCaptchaImage("");
      } else {
        setRegCaptchaId("");
        setRegCaptchaImage("");
      }
      return false;
    }
  }, []);

  useEffect(() => { void loadCaptcha("sms"); }, [loadCaptcha]); // eslint-disable-line react-hooks/set-state-in-effect

  useEffect(() => {
    if (smsCooldown <= 0) return;
    const timer = window.setTimeout(() => setSmsCooldown((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearTimeout(timer);
  }, [smsCooldown]);

  const changePhone = (value: string) => {
    const mustRefreshConsumedCaptcha = smsSent;
    setPhone(value);
    setError("");
    setSmsSent(false);
    setSmsCode("");
    setRegCaptchaId("");
    setRegCaptchaImage("");
    setRegCaptchaCode("");
    if (mustRefreshConsumedCaptcha) {
      setSmsCaptchaId("");
      setSmsCaptchaImage("");
      setSmsCaptchaCode("");
      void loadCaptcha("sms");
    }
  };

  const sendCode = async () => {
    if (!phone.trim() || smsSending || smsCooldown > 0 || !smsCaptchaCode.trim()) return;
    setSmsSending(true);
    setError("");
    try {
      await api.sendSmsCode(phone.trim(), smsCaptchaId, smsCaptchaCode.trim());
      setSmsCooldown(60);
      setSmsSent(true);
      // Keep smsCaptchaCode visible; it has served its purpose for this request.
      // Load registration CAPTCHA for the next step
      if (!(await loadCaptcha("reg"))) {
        setError("短信验证码已发送，但注册验证码加载失败，请点击刷新重试。");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "发送验证码失败");
      await loadCaptcha("sms");
      setSmsCaptchaCode("");
    } finally {
      setSmsSending(false);
    }
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!phone.trim() || !smsCode || !password || !regCaptchaCode.trim() || submitting) return;
    if (!hasAgreed) {
      setError("请先阅读并同意用户协议和隐私政策");
      return;
    }
    if (password !== confirmPassword) {
      setError("两次输入的密码不一致");
      return;
    }
    if (password.length < 8) {
      setError("密码至少8位");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await api.register({
        phone_number: phone.trim(),
        sms_code: smsCode.trim(),
        password,
        display_name: displayName.trim() || undefined,
        captcha_id: regCaptchaId,
        captcha_code: regCaptchaCode.trim(),
      });
      // Registration successful — switch to login tab
      if (onSuccess) onSuccess();
      else onSwitchToLogin();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "注册失败，请稍后重试。");
      // Refresh registration captcha on failure
      await loadCaptcha("reg");
      setRegCaptchaCode("");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={(event) => void submit(event)}>
      <Dialog.Title>注册新账户</Dialog.Title>
      <label>
        <span>手机号</span>
        <input
          type="tel"
          autoComplete="tel"
          autoFocus
          value={phone}
          onChange={(event) => changePhone(event.target.value)}
          disabled={submitting || smsSending}
          placeholder="请输入手机号"
          maxLength={20}
          required
        />
      </label>

      {/* CAPTCHA for sending the SMS code */}
      <label>
        <span>图片验证码</span>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input
            value={smsCaptchaCode}
            onChange={(event) => setSmsCaptchaCode(event.target.value)}
            disabled={submitting}
            placeholder="输入图中字符"
            maxLength={10}
            required
            style={{ flex: 1, minWidth: 0 }}
          />
          {smsCaptchaImage && (
            <img
              src={smsCaptchaImage}
              alt="验证码"
              style={{ height: 36, borderRadius: 4, border: "1px solid var(--border, #d1d5db)", cursor: "pointer" }}
              onClick={() => void loadCaptcha("sms")}
              title="点击刷新"
            />
          )}
          <button
            type="button"
            onClick={() => void loadCaptcha("sms")}
            style={{ background: "none", border: "none", cursor: "pointer", padding: 4, color: "var(--text-secondary, #6b7280)" }}
            title="刷新验证码"
          >
            <RefreshCw size={16} />
          </button>
        </div>
      </label>

      <label>
        <span>短信验证码</span>
        <div style={{ display: "flex", gap: 8 }}>
          <input
            value={smsCode}
            onChange={(event) => setSmsCode(event.target.value)}
            disabled={submitting || !smsSent}
            placeholder="6位验证码"
            maxLength={8}
            required
            style={{ flex: 1, minWidth: 0 }}
          />
          <button
            type="button"
            aria-label="发送验证码"
            onClick={sendCode}
            disabled={smsSending || smsCooldown > 0 || !phone.trim() || !smsCaptchaCode.trim()}
            style={{
              whiteSpace: "nowrap",
              padding: "6px 12px",
              borderRadius: 6,
              border: "1px solid var(--border, #d1d5db)",
              background: smsCooldown > 0 ? "var(--bg-muted, #f3f4f6)" : "var(--accent, #3b82f6)",
              color: smsCooldown > 0 ? "var(--text-secondary, #6b7280)" : "#fff",
              cursor: smsSending || smsCooldown > 0 ? "not-allowed" : "pointer",
              fontSize: 13,
            }}
          >
            {smsSending ? "发送中..." : smsCooldown > 0 ? `${smsCooldown}s` : "发送验证码"}
          </button>
        </div>
      </label>
      <label>
        <span>密码</span>
        <input
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          disabled={submitting}
          placeholder="至少8位"
          maxLength={128}
          required
        />
      </label>
      <label>
        <span>确认密码</span>
        <input
          type="password"
          autoComplete="new-password"
          value={confirmPassword}
          onChange={(event) => setConfirmPassword(event.target.value)}
          disabled={submitting}
          placeholder="再次输入密码"
          maxLength={128}
          required
        />
      </label>
      <label>
        <span>显示名称（选填）</span>
        <input
          value={displayName}
          onChange={(event) => setDisplayName(event.target.value)}
          disabled={submitting}
          placeholder="您的昵称"
          maxLength={128}
        />
      </label>

      {/* CAPTCHA for registration */}
      {smsSent && (
        <label>
          <span>注册验证</span>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input
              value={regCaptchaCode}
              onChange={(event) => setRegCaptchaCode(event.target.value)}
              disabled={submitting}
              placeholder="输入图中字符"
              maxLength={10}
              required
              style={{ flex: 1, minWidth: 0 }}
            />
            {regCaptchaImage && (
              <img
                src={regCaptchaImage}
                alt="注册验证码"
                style={{ width: 160, height: 64, flexShrink: 0, objectFit: "cover", borderRadius: 4, border: "1px solid var(--border, #d1d5db)", cursor: "pointer" }}
                onClick={() => void loadCaptcha("reg")}
                title="点击刷新"
              />
            )}
            <button
              type="button"
              onClick={() => void loadCaptcha("reg")}
              style={{ background: "none", border: "none", cursor: "pointer", padding: 4, color: "var(--text-secondary, #6b7280)" }}
              title="刷新验证码"
            >
              <RefreshCw size={16} />
            </button>
          </div>
        </label>
      )}

      {error && <p className="login-dialog-error" role="alert">{error}</p>}
      <button
        className="login-dialog-submit"
        type="submit"
        disabled={submitting || !phone.trim() || !smsCode || !password || !regCaptchaCode.trim() || !hasAgreed}
      >
        <LockKeyhole size={16} />
        {submitting ? "注册中..." : "注册"}
      </button>
      <ConsentNotice
        id="dialog-register-consent"
        checked={hasAgreed}
        disabled={submitting}
        onChange={(checked) => { setHasAgreed(checked); setError(""); }}
        onOpen={setLegalDocument}
      />
      <LegalDocumentModal document={legalDocument} onClose={() => setLegalDocument(null)} />
      <div style={{ marginTop: 12, textAlign: "center", fontSize: 13, color: "var(--text-secondary, #6b7280)" }}>
        <span>已有账号？</span>{" "}
        <button
          type="button"
          onClick={onSwitchToLogin}
          style={{ background: "none", border: "none", color: "var(--accent, #3b82f6)", cursor: "pointer", fontWeight: 500, padding: 0 }}
        >
          去登录
        </button>
      </div>
    </form>
  );
}
