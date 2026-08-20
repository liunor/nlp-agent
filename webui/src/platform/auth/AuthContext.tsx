import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, ensureAuth } from "@/platform/http/api";
import type { AuthSession } from "@/shared/types";

interface AuthContextValue {
  user: AuthSession | null;
  roles: string[];
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<AuthSession>;
  logout: () => Promise<void>;
  error: string;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * 全局鉴权上下文（满足 review 文档 8.3）。
 * 启动时 bootstrap session，统一暴露 user/roles 与 logout。
 * 注意：前端只做体验，真实权限以服务端返回的 permissions / capabilities 为准。
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthSession | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    ensureAuth()
      .then((session) => {
        if (!active) return;
        setUser(session);
        setError("");
      })
      .catch((e: unknown) => {
        if (!active) return;
        const status = typeof e === "object" && e !== null && "status" in e ? e.status : undefined;
        if (status !== 401) setError(e instanceof Error ? e.message : "认证失败");
      })
      .finally(() => {
        if (active) setIsLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    setIsLoading(true);
    setError("");
    try {
      const session = await api.login(username, password);
      setUser(session);
      return session;
    } catch (reason) {
      setUser(null);
      setError(reason instanceof Error ? reason.message : "登录失败");
      throw reason;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      // A locally expired/revoked cookie must not leave the React tree looking
      // authenticated after the server has rejected logout.
      setUser(null);
      setError("");
    }
  }, []);

  const value = useMemo<AuthContextValue>(() => ({
    user,
    roles: user?.roles ?? [],
    isAuthenticated: user !== null,
    isLoading,
    login,
    logout,
    error,
  }), [error, isLoading, login, logout, user]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth 必须在 <AuthProvider> 内部使用");
  }
  return ctx;
}

export function useOptionalAuth(): AuthContextValue | null {
  return useContext(AuthContext);
}
