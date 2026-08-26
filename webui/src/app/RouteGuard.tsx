import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";

import { useAuth } from "@/platform/auth/AuthContext";

interface RouteGuardProps {
  allowedRoles: string[];
  children: ReactNode;
}

/**
 * Role-based route guard.
 * Renders children when the current user holds at least one of the
 * required roles; otherwise redirects to the student home page.
 */
export function RouteGuard({ allowedRoles, children }: RouteGuardProps) {
  const { roles, isAuthenticated, isLoading } = useAuth();

  if (isLoading || !isAuthenticated) {
    return null;
  }

  const hasRole = allowedRoles.some((r) => roles.includes(r));
  if (!hasRole) {
    return <Navigate to="/" replace />;
  }

  return <>{children}</>;
}
