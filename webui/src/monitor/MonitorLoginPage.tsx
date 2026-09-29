import { ArrowRight, Gauge, KeyRound, ShieldCheck } from "lucide-react";
import { FormEvent, useState } from "react";

interface MonitorLoginPageProps {
  message?: string;
  onLogin: (username: string, password: string) => Promise<void>;
}

function authErrorMessage(reason: unknown): string {
  const status = typeof reason === "object" && reason !== null && "status" in reason
    ? (reason as { status?: unknown }).status
    : undefined;
  if (status === 401) return "账号或密码错误，请重试。";
  if (status === 403) return "当前账号没有监控权限，请使用开发者账号登录。";
  return reason instanceof Error ? reason.message : "登录失败，请稍后重试。";
}

export function MonitorLoginPage({ message, onLogin }: MonitorLoginPageProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError("");
    try {
      await onLogin(username.trim(), password);
    } catch (reason) {
      setError(authErrorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="monitor-auth-shell">
      <section className="monitor-auth-card" aria-labelledby="monitor-login-title">
        <div className="monitor-auth-intro">
          <div className="monitor-auth-mark"><Gauge size={20} /></div>
          <span className="monitor-auth-kicker">NLP MONITOR · 8766</span>
          <h1>运行观测平台</h1>
          <p>查看 Agent、Worker、模型调用和代码沙箱的实时运行状态。</p>
          <div className="monitor-auth-points">
            <span><ShieldCheck size={16} />同一套平台账号</span>
            <span><KeyRound size={16} />仅开放给开发者</span>
          </div>
        </div>
        <form className="monitor-auth-form" onSubmit={handleSubmit}>
          <div>
            <span className="monitor-auth-label">SECURE ACCESS</span>
            <h2 id="monitor-login-title">登录监控平台</h2>
            <p>使用主页面的账号和密码即可登录。</p>
          </div>
          {(error || message) && <div className="monitor-auth-error" role="alert" aria-live="polite">{error || message}</div>}
          <label htmlFor="monitor-login-username">用户名</label>
          <input id="monitor-login-username" type="text" required autoFocus autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} disabled={submitting} />
          <label htmlFor="monitor-login-password">密码</label>
          <input id="monitor-login-password" type="password" required autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} disabled={submitting} />
          <button type="submit" disabled={submitting}>
            {submitting ? "登录中…" : "登录监控平台"}
            {!submitting && <ArrowRight size={16} />}
          </button>
        </form>
      </section>
    </main>
  );
}
