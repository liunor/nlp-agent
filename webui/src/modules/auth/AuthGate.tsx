import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "@/platform/auth/AuthContext";

/**
 * Redirects unauthenticated users to /login.
 * Shows a loading spinner while auth state is being resolved.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50">
        <div className="text-center space-y-3">
          <div className="inline-block h-8 w-8 animate-spin rounded-full border-4 border-blue-600 border-t-transparent" />
          <p className="text-sm text-gray-500">正在验证身份…</p>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <>{children}</>;
}
