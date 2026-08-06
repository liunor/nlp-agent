import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/platform/auth/AuthContext";

export function LoginPage() {
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
      navigate("/", { replace: true });
    } catch {
      // error is set by AuthContext
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50">
      <div className="w-full max-w-sm space-y-6 rounded-lg bg-white p-8 shadow-md">
        <div className="text-center">
          <h1 className="text-2xl font-bold text-gray-900">NLP 学习平台</h1>
          <p className="mt-1 text-sm text-gray-500">请登录以继续</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {(error || (submitting && false)) && (
            <div className="rounded bg-red-50 p-3 text-sm text-red-700">
              {error ?? "登录失败"}
            </div>
          )}

          <div>
            <label htmlFor="username" className="block text-sm font-medium text-gray-700">
              用户名
            </label>
            <input
              id="username"
              type="text"
              required
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              disabled={submitting}
            />
          </div>

          <div>
            <label htmlFor="password" className="block text-sm font-medium text-gray-700">
              密码
            </label>
            <input
              id="password"
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              disabled={submitting}
            />
          </div>

          <button
            type="submit"
            disabled={submitting || isLoading}
            className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {submitting ? "登录中..." : "登录"}
          </button>
        </form>
      </div>
    </div>
  );
}
