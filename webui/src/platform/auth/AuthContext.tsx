import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import {
  api,
  AuthStateChangedError,
  clearCsrfToken,
  ensureAuth,
  getAuthGeneration,
  subscribeAuthChanges,
  type AuthRequiredEventDetail,
} from "@/platform/http/api";
import { AUTH_EXPIRED_EVENT, AUTH_REQUIRED_EVENT } from "@/platform/auth/auth-events";
import type { AuthSession } from "@/shared/types";

interface AuthContextValue {
  user: AuthSession | null;
  roles: string[];
  isAuthenticated: boolean;
  isLoading: boolean;
  isAuthExpired: boolean;
  login: (username: string, password: string) => Promise<AuthSession>;
  refreshSession: () => Promise<AuthSession>;
  logout: () => Promise<void>;
  error: string;
  authNotice: string;
  clearAuthNotice: () => void;
}

const AUTH_EVENT_PRIORITY: Record<string, number> = {
  authentication_required: 1,
  session_expired: 2,
  authorization_changed: 3,
};

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
  const [authNotice, setAuthNotice] = useState("");
  const lastAuthEvent = useRef<{ code: string; at: number }>({ code: "", at: 0 });
  const authNoticePriority = useRef(0);

  const clearAuthNotice = useCallback(() => {
    authNoticePriority.current = 0;
    lastAuthEvent.current = { code: "", at: 0 };
    setAuthNotice("");
  }, []);

  useEffect(() => {
    const handleAuthRequired = (event: Event) => {
      const detail = (event as CustomEvent<AuthRequiredEventDetail>).detail;
      if (!detail) return;
      const now = Date.now();
      const code = detail.code || "authentication_required";
      const priority = AUTH_EVENT_PRIORITY[code] ?? AUTH_EVENT_PRIORITY.authentication_required;
      const previous = lastAuthEvent.current;
      if (previous.code === code && now - previous.at < 1000) return;
      if (priority < authNoticePriority.current) return;
      lastAuthEvent.current = { code, at: now };
      authNoticePriority.current = Math.max(authNoticePriority.current, priority);
      clearCsrfToken();
      setUser(null);
      const shouldExplain = code === "authorization_changed" || code === "session_expired";
      if (shouldExplain) setIsAuthExpired(true);
      setError(shouldExplain ? detail.message : "");
      setAuthNotice(shouldExplain ? detail.message : "");
    };
    window.addEventListener(AUTH_REQUIRED_EVENT, handleAuthRequired);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, handleAuthRequired);
  }, []);

  useEffect(() => {
    const handleAuthExpired = () => {
      setIsAuthExpired(true);
      setError("");
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired);
  }, []);

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
          clearAuthNotice();
        })
        .catch((reason: unknown) => {
          if (!active || generation !== getAuthGeneration() || reason instanceof AuthStateChangedError) return;
          const status = typeof reason === "object" && reason !== null && "status" in reason ? reason.status : undefined;
          if (status !== 401) setError(reason instanceof Error ? reason.message : "认证失败");
        })
        .finally(() => {
          if (active && generation === getAuthGeneration()) setIsLoading(false);
        });
    };
    const unsubscribe = subscribeAuthChanges(() => {
      setUser(null);
      setError("");
      setIsAuthExpired(false);
      clearAuthNotice();
      setIsLoading(true);
      void restore();
    });
    void restore();
    return () => {
      active = false;
      unsubscribe();
    };
  }, [clearAuthNotice]);

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
      timeoutId = window.setTimeout(scheduleExpiration, Math.min(remainingMs, maxTimeoutMs));
    };

    timeoutId = window.setTimeout(scheduleExpiration, Math.min(expiresInMs, maxTimeoutMs));
    return () => window.clearTimeout(timeoutId);
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
      clearAuthNotice();
      return session;
    } catch (reason) {
      if (generation === getAuthGeneration() && !(reason instanceof AuthStateChangedError)) {
        setError(reason instanceof Error ? reason.message : "登录失败");
        setIsLoading(false);
      }
      throw reason;
    }
  }, [clearAuthNotice]);

  const refreshSession = useCallback(async () => {
    const generation = getAuthGeneration();
    const session = await ensureAuth();
    if (generation !== getAuthGeneration()) throw new AuthStateChangedError();
    setUser(session);
    setError("");
    setIsAuthExpired(false);
    clearAuthNotice();
    return session;
  }, [clearAuthNotice]);

  const logout = useCallback(async () => {
    const pending = api.logout();
    const generation = getAuthGeneration();
    try {
      await pending;
    } catch (err) {
      console.error("Logout failed:", err);
    } finally {
      if (generation === getAuthGeneration()) {
        setUser(null);
        setError("");
        setIsAuthExpired(false);
        clearAuthNotice();
        window.location.href = "/";
      }
    }
  }, [clearAuthNotice]);

  const value = useMemo<AuthContextValue>(() => ({
    user,
    roles: user?.roles ?? [],
    isAuthenticated: user !== null,
    isLoading,
    isAuthExpired,
    login,
    refreshSession,
    logout,
    error,
    authNotice,
    clearAuthNotice,
  }), [authNotice, clearAuthNotice, error, isAuthExpired, isLoading, login, logout, refreshSession, user]);

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
