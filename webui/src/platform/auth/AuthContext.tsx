import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "@/platform/http/api";
import type { MeResponse } from "@/shared/types";

interface AuthState {
  user: MeResponse | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
}

interface AuthActions {
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

type AuthContextValue = AuthState & AuthActions;

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<MeResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const me = await api.getMe();
      setUser(me);
      setError(null);
    } catch {
      setUser(null);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await api.getAuthSession();
        if (!cancelled) await refresh();
      } catch {
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [refresh]);

  const login = useCallback(async (username: string, password: string) => {
    setError(null);
    try {
      await api.login(username, password);
      await refresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : "登录失败";
      setError(message);
      throw err;
    }
  }, [refresh]);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setUser(null);
    }
  }, []);

  const value = useMemo<AuthContextValue>(() => ({
    user,
    isAuthenticated: user !== null,
    isLoading,
    error,
    login,
    logout,
    refresh,
  }), [user, isLoading, error, login, logout, refresh]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
