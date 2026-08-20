import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { useAuth } from "@/platform/auth/AuthContext";

function currentLocation(location: ReturnType<typeof useLocation>): string {
  return `${location.pathname}${location.search}${location.hash}`;
}

export function AuthGate({ children }: { children: ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="boot-screen" role="status">
        <span className="boot-orbit" />
        <strong>正在进入 NLP 学习空间</strong>
        <p>正在验证登录状态……</p>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: currentLocation(location) }} />;
  }

  return <>{children}</>;
}
