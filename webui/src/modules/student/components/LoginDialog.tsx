import * as Dialog from "@radix-ui/react-dialog";
import { LockKeyhole, RefreshCw, X } from "lucide-react";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/platform/http/api";

type Tab = "login" | "register";

interface LoginDialogProps {
  open: boolean;
  expired?: boolean;
  onClose: () => void;
  onAuthenticate: (username: string, password: string) => Promise<void>;
}

export function LoginDialog({ open, expired = false, onClose, onAuthenticate }: LoginDialogProps) {
  const [tab, setTab] = useState<Tab>("login");

  const close = useCallback(() => {
    setTab("login");
    onClose();
  }, [onClose]);
  const dismiss = useCallback(() => {
  if (expired) return;
  close();
}, [close, expired]);

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

          {tab === "login" ? (
            <LoginForm onAuthenticate={onAuthenticate} onSuccess={close} onSwitchToRegister={() => setTab("register")} />
          ) : (
            <RegisterForm onSwitchToLogin={() => setTab("login")} />
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
  onSuccess,
  onSwitchToRegister,
}: {
  onAuthenticate: (username: string, password: string) => Promise<void>;
  onSuccess: () => void;
  onSwitchToRegister: () => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!username.trim() || !password || submitting) return;
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
        disabled={submitting || !username.trim() || !password}
      >
        <LockKeyhole size={16} />
        {submitting ? "正在验证" : "登录并继续"}
      </button>
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
}: {
  onSwitchToLogin: () => void;
}) {
  const [email, setEmail] = useState("");
  const [emailCode, setEmailCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [emailSending, setEmailSending] = useState(false);
  const [emailCooldown, setEmailCooldown] = useState(0);

  // CAPTCHA state — two separate captchas: one for the email code, one for registration
  const [emailCaptchaId, setEmailCaptchaId] = useState("");
  const [emailCaptchaImage, setEmailCaptchaImage] = useState("");
  const [emailCaptchaCode, setEmailCaptchaCode] = useState("");
  const [regCaptchaId, setRegCaptchaId] = useState("");
  const [regCaptchaImage, setRegCaptchaImage] = useState("");
  const [regCaptchaCode, setRegCaptchaCode] = useState("");
  const [emailSent, setEmailSent] = useState(false);
  const captchaRequestVersion = useRef({ email: 0, reg: 0 });

  const loadCaptcha = useCallback(async (target: "email" | "reg") => {
    const requestVersion = ++captchaRequestVersion.current[target];
    try {
      const resp = await api.getCaptcha();
      if (requestVersion !== captchaRequestVersion.current[target]) return true;
      if (target === "email") {
        setEmailCaptchaId(resp.captcha_id);
        setEmailCaptchaImage(resp.image);
      } else {
        setRegCaptchaId(resp.captcha_id);
        setRegCaptchaImage(resp.image);
      }
      return true;
    } catch {
      if (requestVersion !== captchaRequestVersion.current[target]) return true;
      if (target === "email") {
        setEmailCaptchaId("");
        setEmailCaptchaImage("");
      } else {
        setRegCaptchaId("");
        setRegCaptchaImage("");
      }
      return false;
    }
  }, []);

  useEffect(() => { void loadCaptcha("email"); }, [loadCaptcha]); // eslint-disable-line react-hooks/set-state-in-effect

  useEffect(() => {
    if (emailCooldown <= 0) return;
    const timer = window.setTimeout(() => setEmailCooldown((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearTimeout(timer);
  }, [emailCooldown]);

  const changeEmail = (value: string) => {
    const mustRefreshConsumedCaptcha = emailSent;
    setEmail(value);
    setError("");
    setEmailSent(false);
    setEmailCode("");
    setRegCaptchaId("");
    setRegCaptchaImage("");
    setRegCaptchaCode("");
    if (mustRefreshConsumedCaptcha) {
      setEmailCaptchaId("");
      setEmailCaptchaImage("");
      setEmailCaptchaCode("");
      void loadCaptcha("email");
    }
  };

  const sendCode = async () => {
    if (!email.trim() || emailSending || emailCooldown > 0 || !emailCaptchaCode.trim()) return;
    setEmailSending(true);
    setError("");
    try {
      await api.sendEmailCode(email.trim(), emailCaptchaId, emailCaptchaCode.trim());
      setEmailCooldown(60);
      setEmailSent(true);
      // Keep emailCaptchaCode value (don't clear it) - user may need to see what they entered
      // Load registration CAPTCHA for the next step
      if (!(await loadCaptcha("reg"))) {
        setError("邮箱验证码已发送，但注册验证码加载失败，请点击刷新重试。");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "发送验证码失败");
      // Refresh email captcha on failure
      await loadCaptcha("email");
      setEmailCaptchaCode("");
    } finally {
      setEmailSending(false);
    }
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!email.trim() || !emailCode || !password || !regCaptchaCode.trim() || submitting) return;
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
        email: email.trim(),
        email_code: emailCode.trim(),
        password,
        display_name: displayName.trim() || undefined,
        captcha_id: regCaptchaId,
        captcha_code: regCaptchaCode.trim(),
      });
      // Registration successful — switch to login tab
      onSwitchToLogin();
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
        <span>邮箱</span>
        <input
          type="email"
          autoComplete="email"
          autoFocus
          value={email}
          onChange={(event) => changeEmail(event.target.value)}
          disabled={submitting || emailSending}
          placeholder="请输入邮箱"
          maxLength={254}
          required
        />
      </label>

      {/* CAPTCHA for sending the email code */}
      <label>
        <span>图片验证码</span>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input
            value={emailCaptchaCode}
            onChange={(event) => setEmailCaptchaCode(event.target.value)}
            disabled={submitting}
            placeholder="输入图中字符"
            maxLength={10}
            required
            style={{ flex: 1, minWidth: 0 }}
          />
          {emailCaptchaImage && (
            <img
              src={emailCaptchaImage}
              alt="验证码"
              style={{ width: 160, height: 64, flexShrink: 0, objectFit: "cover", borderRadius: 4, border: "1px solid var(--border, #d1d5db)", cursor: "pointer" }}
              onClick={() => void loadCaptcha("email")}
              title="点击刷新"
            />
          )}
          <button
            type="button"
            onClick={() => void loadCaptcha("email")}
            style={{ background: "none", border: "none", cursor: "pointer", padding: 4, color: "var(--text-secondary, #6b7280)" }}
            title="刷新验证码"
          >
            <RefreshCw size={16} />
          </button>
        </div>
      </label>

      <label>
        <span>邮箱验证码</span>
        <div style={{ display: "flex", gap: 8 }}>
          <input
            value={emailCode}
            onChange={(event) => setEmailCode(event.target.value)}
            disabled={submitting || !emailSent}
            placeholder="6位验证码"
            maxLength={8}
            required
            style={{ flex: 1, minWidth: 0 }}
          />
          <button
            type="button"
            aria-label="发送验证码"
            onClick={sendCode}
            disabled={emailSending || emailCooldown > 0 || !email.trim() || !emailCaptchaCode.trim()}
            style={{
              whiteSpace: "nowrap",
              padding: "6px 12px",
              borderRadius: 6,
              border: "1px solid var(--border, #d1d5db)",
              background: emailCooldown > 0 ? "var(--bg-muted, #f3f4f6)" : "var(--accent, #3b82f6)",
              color: emailCooldown > 0 ? "var(--text-secondary, #6b7280)" : "#fff",
              cursor: emailSending || emailCooldown > 0 ? "not-allowed" : "pointer",
              fontSize: 13,
            }}
          >
            {emailSending ? "发送中..." : emailCooldown > 0 ? `${emailCooldown}s` : "发送验证码"}
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
      {emailSent && (
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
        disabled={submitting || !email.trim() || !emailCode || !password || !regCaptchaCode.trim()}
      >
        <LockKeyhole size={16} />
        {submitting ? "注册中..." : "注册"}
      </button>
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
