import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import { useAuth } from "@/platform/auth/AuthContext";
import { api } from "@/platform/http/api";

type Tab = "login" | "register";

function safeReturnPath(value: unknown): string {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return "/";
  return value;
}

export function LoginPage() {
  const { isAuthenticated, isLoading } = useAuth();
  const location = useLocation();
  const [tab, setTab] = useState<Tab>("login");
  const from = safeReturnPath((location.state as { from?: unknown } | null)?.from);

  if (!isLoading && isAuthenticated) {
    return <Navigate to={from} replace />;
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50">
      <div className="w-full max-w-sm rounded-lg bg-white p-8 shadow-md">
        <div className="mb-6 text-center">
          <h1 className="text-2xl font-bold text-gray-900">NLP 学习平台</h1>
          <p className="mt-1 text-sm text-gray-500">
            {tab === "login" ? "登录后可开始学习" : "注册新账户"}
          </p>
        </div>

        {tab === "login" ? (
          <LoginForm returnPath={from} />
        ) : (
          <RegisterForm onSuccess={() => setTab("login")} />
        )}

        {/* Tab switcher at bottom */}
        <div className="mt-6 flex items-center justify-center gap-3 border-t border-gray-100 pt-4">
          <span className="text-sm text-gray-500">
            {tab === "login" ? "还没有账号？" : "已有账号？"}
          </span>
          <button
            type="button"
            onClick={() => setTab(tab === "login" ? "register" : "login")}
            className="text-sm font-medium text-blue-600 hover:text-blue-700"
          >
            {tab === "login" ? "立即注册" : "去登录"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// LoginForm
// ---------------------------------------------------------------------------

function LoginForm({ returnPath }: { returnPath: string }) {
  const { login, error, isLoading } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      await login(username, password);
      navigate(returnPath, { replace: true });
    } catch {
      // error is set by AuthContext
    } finally {
      setSubmitting(false);
    }
  };

  const inputCls = "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-gray-50";

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && (
        <div className="rounded bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <div>
        <label htmlFor="login-email" className="block text-sm font-medium text-gray-700">邮箱</label>
        <input id="login-email" type="text" required autoComplete="username" autoFocus
          value={username} onChange={(e) => setUsername(e.target.value)}
          disabled={submitting} placeholder="请输入邮箱" className={inputCls} />
      </div>

      <div>
        <label htmlFor="login-password" className="block text-sm font-medium text-gray-700">密码</label>
        <input id="login-password" type="password" required autoComplete="current-password"
          value={password} onChange={(e) => setPassword(e.target.value)}
          disabled={submitting} className={inputCls} />
      </div>

      <button type="submit" disabled={submitting || isLoading}
        className="w-full rounded-md bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 transition-colors">
        {submitting ? "登录中..." : "登录"}
      </button>
    </form>
  );
}

// ---------------------------------------------------------------------------
// RegisterForm
// ---------------------------------------------------------------------------

function RegisterForm({ onSuccess }: { onSuccess: () => void }) {
  const [email, setEmail] = useState("");
  const [emailCode, setEmailCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [emailSending, setEmailSending] = useState(false);
  const [emailCooldown, setEmailCooldown] = useState(0);
  const [emailSent, setEmailSent] = useState(false);

  const [emailCaptchaId, setEmailCaptchaId] = useState("");
  const [emailCaptchaImage, setEmailCaptchaImage] = useState("");
  const [emailCaptchaCode, setEmailCaptchaCode] = useState("");
  const [regCaptchaId, setRegCaptchaId] = useState("");
  const [regCaptchaImage, setRegCaptchaImage] = useState("");
  const [regCaptchaCode, setRegCaptchaCode] = useState("");
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
      // 保留用户已输入的图片验证码字符串，不在此处清空
      if (!(await loadCaptcha("reg"))) {
        setError("邮箱验证码已发送，但注册验证码加载失败，请点击刷新重试。");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "发送验证码失败");
      await loadCaptcha("email");
      setEmailCaptchaCode("");
    } finally {
      setEmailSending(false);
    }
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !emailCode || !password || !regCaptchaCode.trim() || submitting) return;
    if (password !== confirmPassword) { setError("两次输入的密码不一致"); return; }
    if (password.length < 8) { setError("密码至少8位"); return; }
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
      onSuccess();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "注册失败，请稍后重试。");
      await loadCaptcha("reg");
      setRegCaptchaCode("");
    } finally {
      setSubmitting(false);
    }
  };

  const inputCls = "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-gray-50";
  const captchaImgCls = "h-16 w-40 shrink-0 rounded border border-gray-300 object-cover cursor-pointer";

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && <div className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      <div>
        <label htmlFor="reg-email" className="block text-sm font-medium text-gray-700">邮箱</label>
        <input id="reg-email" type="email" autoComplete="email" autoFocus required value={email}
          onChange={(e) => changeEmail(e.target.value)} disabled={submitting || emailSending} placeholder="请输入邮箱" maxLength={254} className={inputCls} />
      </div>

      {/* CAPTCHA for email code */}
      <div>
        <label className="block text-sm font-medium text-gray-700">图片验证码</label>
        <div className="mt-1 flex items-center gap-2">
          <input type="text" required value={emailCaptchaCode}
            onChange={(e) => setEmailCaptchaCode(e.target.value)} disabled={submitting}
            placeholder="输入图中字符" maxLength={10} className={`${inputCls} min-w-0 flex-1`} />
          {emailCaptchaImage && (
            <img src={emailCaptchaImage} alt="验证码" className={captchaImgCls}
              onClick={() => void loadCaptcha("email")} title="点击刷新" />
          )}
          <button type="button" onClick={() => void loadCaptcha("email")}
            className="shrink-0 rounded p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600" title="刷新">
            <RefreshCw size={16} />
          </button>
        </div>
      </div>

      {/* Email code */}
      <div>
        <label className="block text-sm font-medium text-gray-700">邮箱验证码</label>
        <div className="mt-1 flex gap-2">
          <input type="text" required value={emailCode}
            onChange={(e) => setEmailCode(e.target.value)} disabled={submitting || !emailSent}
            placeholder="6位验证码" maxLength={8} className={inputCls} />
          <button type="button" onClick={sendCode}
            disabled={emailSending || emailCooldown > 0 || !email.trim() || !emailCaptchaCode.trim()}
            className="shrink-0 whitespace-nowrap rounded-md border border-gray-300 px-3 py-2 text-xs font-medium text-white transition-colors disabled:cursor-not-allowed"
            style={{
              background: emailCooldown > 0 ? "#f3f4f6" : "#3b82f6",
              color: emailCooldown > 0 ? "#6b7280" : "#fff",
            }}>
            {emailSending ? "发送中..." : emailCooldown > 0 ? `${emailCooldown}s` : "发送验证码"}
          </button>
        </div>
      </div>

      <div>
        <label htmlFor="reg-password" className="block text-sm font-medium text-gray-700">密码</label>
        <input id="reg-password" type="password" autoComplete="new-password" required value={password}
          onChange={(e) => setPassword(e.target.value)} disabled={submitting} placeholder="至少8位" maxLength={128} className={inputCls} />
      </div>

      <div>
        <label htmlFor="reg-confirm" className="block text-sm font-medium text-gray-700">确认密码</label>
        <input id="reg-confirm" type="password" autoComplete="new-password" required value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)} disabled={submitting} placeholder="再次输入密码" maxLength={128} className={inputCls} />
      </div>

      <div>
        <label htmlFor="reg-name" className="block text-sm font-medium text-gray-700">显示名称（选填）</label>
        <input id="reg-name" type="text" value={displayName}
          onChange={(e) => setDisplayName(e.target.value)} disabled={submitting} placeholder="您的昵称" maxLength={128} className={inputCls} />
      </div>

      {/* CAPTCHA for registration */}
      {emailSent && (
        <div>
          <label className="block text-sm font-medium text-gray-700">注册验证</label>
          <div className="mt-1 flex items-center gap-2">
            <input type="text" required value={regCaptchaCode}
              onChange={(e) => setRegCaptchaCode(e.target.value)} disabled={submitting}
              placeholder="输入图中字符" maxLength={10} className={`${inputCls} min-w-0 flex-1`} />
            {regCaptchaImage && (
              <img src={regCaptchaImage} alt="注册验证码" className={captchaImgCls}
                onClick={() => void loadCaptcha("reg")} title="点击刷新" />
            )}
            <button type="button" onClick={() => void loadCaptcha("reg")}
              className="shrink-0 rounded p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600" title="刷新">
              <RefreshCw size={16} />
            </button>
          </div>
        </div>
      )}

      <button type="submit" disabled={submitting || !email.trim() || !emailCode || !password || !regCaptchaCode.trim()}
        className="w-full rounded-md bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 transition-colors">
        {submitting ? "注册中..." : "注册"}
      </button>
    </form>
  );
}
