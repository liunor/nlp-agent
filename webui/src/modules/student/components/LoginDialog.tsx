import * as Dialog from "@radix-ui/react-dialog";
import { LockKeyhole, RefreshCw, X } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "@/platform/http/api";

type Tab = "login" | "register";

interface LoginDialogProps {
  open: boolean;
  onClose: () => void;
  onAuthenticate: (username: string, password: string) => Promise<void>;
}

export function LoginDialog({ open, onClose, onAuthenticate }: LoginDialogProps) {
  const [tab, setTab] = useState<Tab>("login");

  const close = useCallback(() => {
    setTab("login");
    onClose();
  }, [onClose]);

  return (
    <Dialog.Root open={open} onOpenChange={(nextOpen: boolean) => { if (!nextOpen) close(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="login-dialog-overlay" />
        <Dialog.Content className="login-dialog-content" aria-describedby="login-dialog-description">
          <button className="login-dialog-close" type="button" onClick={close} aria-label="关闭">
            <X size={18} />
          </button>

          <Dialog.Description id="login-dialog-description">
            {tab === "login"
              ? "登录后可创建学习会话并使用实时对话功能。"
              : "使用手机号注册新账户，开始您的学习之旅。"}
          </Dialog.Description>

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
        <span>账号</span>
        <input
          autoComplete="username"
          autoFocus
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          disabled={submitting}
          maxLength={128}
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
  const [phone, setPhone] = useState("");
  const [smsCode, setSmsCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [smsSending, setSmsSending] = useState(false);
  const [smsCooldown, setSmsCooldown] = useState(0);

  // CAPTCHA state — two separate captchas: one for SMS, one for registration
  const [smsCaptchaId, setSmsCaptchaId] = useState("");
  const [smsCaptchaImage, setSmsCaptchaImage] = useState("");
  const [smsCaptchaCode, setSmsCaptchaCode] = useState("");
  const [regCaptchaId, setRegCaptchaId] = useState("");
  const [regCaptchaImage, setRegCaptchaImage] = useState("");
  const [regCaptchaCode, setRegCaptchaCode] = useState("");
  const [smsSent, setSmsSent] = useState(false);

  const loadCaptcha = useCallback(async (target: "sms" | "reg") => {
    try {
      const resp = await api.getCaptcha();
      if (target === "sms") {
        setSmsCaptchaId(resp.captcha_id);
        setSmsCaptchaImage(resp.image);
      } else {
        setRegCaptchaId(resp.captcha_id);
        setRegCaptchaImage(resp.image);
      }
    } catch {
      // silently fail — user can retry by clicking refresh
    }
  }, []);

  useEffect(() => { void loadCaptcha("sms"); }, [loadCaptcha]); // eslint-disable-line react-hooks/set-state-in-effect

  const sendCode = async () => {
    if (!phone.trim() || smsSending || smsCooldown > 0 || !smsCaptchaCode.trim()) return;
    setSmsSending(true);
    setError("");
    try {
      await api.sendSmsCode(phone.trim(), smsCaptchaId, smsCaptchaCode.trim());
      setSmsCooldown(60);
      setSmsSent(true);
      // Keep smsCaptchaCode value (don't clear it) - user may need to see what they entered
      // Load registration CAPTCHA for the next step
      await loadCaptcha("reg");
      const timer = setInterval(() => {
        setSmsCooldown((prev) => {
          if (prev <= 1) {
            clearInterval(timer);
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "发送验证码失败");
      // Refresh SMS captcha on failure
      await loadCaptcha("sms");
      setSmsCaptchaCode("");
    } finally {
      setSmsSending(false);
    }
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!phone.trim() || !smsCode || !password || !regCaptchaCode.trim() || submitting) return;
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
        <span>手机号</span>
        <input
          autoComplete="tel"
          autoFocus
          value={phone}
          onChange={(event) => setPhone(event.target.value)}
          disabled={submitting}
          placeholder="请输入手机号"
          maxLength={20}
          required
        />
      </label>

      {/* CAPTCHA for sending SMS */}
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
            style={{ flex: 1 }}
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
            style={{ flex: 1 }}
          />
          <button
            type="button"
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
      {smsSent && regCaptchaImage && (
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
              style={{ flex: 1 }}
            />
            <img
              src={regCaptchaImage}
              alt="注册验证码"
              style={{ height: 36, borderRadius: 4, border: "1px solid var(--border, #d1d5db)", cursor: "pointer" }}
              onClick={() => void loadCaptcha("reg")}
              title="点击刷新"
            />
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
        disabled={submitting || !phone.trim() || !smsCode || !password || !regCaptchaCode.trim()}
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
