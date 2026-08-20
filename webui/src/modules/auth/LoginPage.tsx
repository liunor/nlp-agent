import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "@/platform/auth/AuthContext";

function safeReturnPath(value: unknown): string {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return "/";
  return value;
}

export function LoginPage() {
  const { login, error, isAuthenticated, isLoading } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const from = safeReturnPath((location.state as { from?: unknown } | null)?.from);

  if (!isLoading && isAuthenticated) {
    return <Navigate to={from} replace />;
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    try {
      await login(username.trim(), password);
      navigate(from, { replace: true });
    } catch {
      // AuthContext owns the user-visible error so every login surface behaves
      // consistently with the database-backed session state.
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <section className="w-full max-w-sm space-y-6 rounded-lg bg-white p-8 shadow-md" aria-labelledby="login-title">
        <div className="text-center">
          <h1 id="login-title" className="text-2xl font-bold text-gray-900">NLP 学习平台</h1>
          <p className="mt-1 text-sm text-gray-500">请使用平台账号登录</p>
        </div>

        <form onSubmit={(event) => void submit(event)} className="space-y-4">
          {error && <div className="rounded bg-red-50 p-3 text-sm text-red-700" role="alert">{error}</div>}
          <label className="block text-sm font-medium text-gray-700">
            用户名
            <input
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              required
              disabled={submitting || isLoading}
            />
          </label>
          <label className="block text-sm font-medium text-gray-700">
            密码
            <input
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              required
              disabled={submitting || isLoading}
            />
          </label>
          <button
            className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            type="submit"
            disabled={submitting || isLoading || !username.trim() || !password}
          >
            {submitting ? "登录中…" : "登录"}
          </button>
        </form>
      </section>
    </main>
  );
}
