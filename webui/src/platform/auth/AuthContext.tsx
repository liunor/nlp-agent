import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, AUTH_EXPIRED_EVENT, AuthStateChangedError, ensureAuth, getAuthGeneration, subscribeAuthChanges } from "@/platform/http/api";
import type { AuthSession } from "@/shared/types";

interface AuthContextValue {
  user: AuthSession | null;
  roles: string[];
  isAuthenticated: boolean;
  isLoading: boolean;
  isAuthExpired: boolean;
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
  const [isAuthExpired, setIsAuthExpired] = useState(false);

  useEffect(() => {
    let active = true;
    const restore = () => {
      const generation = getAuthGeneration();
      return ensureAuth()
      .then((session) => {
        if (!active || generation !== getAuthGeneration()) return;
        setUser(session);
        setError("");
        setIsAuthExpired(false);
      })
      .catch((e: unknown) => {
        if (!active || generation !== getAuthGeneration() || e instanceof AuthStateChangedError) return;
        const status = typeof e === "object" && e !== null && "status" in e ? e.status : undefined;
        if (status !== 401) setError(e instanceof Error ? e.message : "认证失败");
      })
      .finally(() => {
        if (active && generation === getAuthGeneration()) setIsLoading(false);
      });
    };
    const unsubscribe = subscribeAuthChanges(() => {
      setUser(null);
      setError("");
      setIsAuthExpired(false);
      setIsLoading(true);
      void restore();
    });
    void restore();
    return () => {
      active = false;
      unsubscribe();
    };
  }, []);
  useEffect(() => {
  const handleAuthExpired = () => {
    setIsAuthExpired(true);
    setError("");
  };

  window.addEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired);

  return () => {
    window.removeEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired);
  };
}, []);
      useEffect(() => {
    if (!user || isAuthExpired) return undefined;

    const expiresAtMs = user.expires_at * 1000;
    if (!Number.isFinite(expiresAtMs)) return undefined;

    const expiresInMs = expiresAtMs - Date.now();
    if (expiresInMs <= 0) return undefined;

    const maxTimeoutMs = 2_147_483_647;
    let timeoutId: number;

    const scheduleExpiration = () => {
      const remainingMs = expiresAtMs - Date.now();

      if (remainingMs <= 0) {
        setIsAuthExpired(true);
        setError("");
        return;
      }

      timeoutId = window.setTimeout(
        scheduleExpiration,
        Math.min(remainingMs, maxTimeoutMs),
      );
    };

    timeoutId = window.setTimeout(
      scheduleExpiration,
      Math.min(expiresInMs, maxTimeoutMs),
    );

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [isAuthExpired, user]);
  const login = useCallback(async (username: string, password: string) => {
    setError("");
    const pending = api.login(username, password);
    const generation = getAuthGeneration();
    try {
      const session = await pending;
      if (generation !== getAuthGeneration()) throw new AuthStateChangedError();
      setUser(session);
      setIsAuthExpired(false);
      setIsLoading(false);
      return session;
    } catch (reason) {
      if (generation === getAuthGeneration() && !(reason instanceof AuthStateChangedError)) {
        setError(reason instanceof Error ? reason.message : "登录失败");
        setIsLoading(false);
      }
      throw reason;
    }
  }, []);

  const logout = useCallback(async () => {
    const pending = api.logout();
    const generation = getAuthGeneration();
    try {
      await pending;
    } catch (err) {
      console.error("Logout failed:", err);
    } finally {
      // A locally expired/revoked cookie must not leave the React tree looking
      // authenticated after the server has rejected logout.
      if (generation === getAuthGeneration()) {
        setUser(null);
        setError("");
        // Redirect to login page
        window.location.href = "/";
      }
    }
  }, []);

  const value = useMemo<AuthContextValue>(() => ({
    user,
    roles: user?.roles ?? [],
    isAuthenticated: user !== null,
    isLoading,
    isAuthExpired,
    login,
    logout,
    error,
  }), [error, isAuthExpired, isLoading, login, logout, user]);

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
