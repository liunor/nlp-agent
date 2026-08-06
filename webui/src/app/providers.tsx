import type { ReactNode } from "react";

import { AuthProvider } from "@/platform/auth/AuthContext";
import { AppErrorBoundary } from "@/shared/ui/AppErrorBoundary";
import "@/shared/i18n";
import { StaticUiBridge } from "@/shared/i18n/StaticUiBridge";

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <StaticUiBridge>
        <AppErrorBoundary>{children}</AppErrorBoundary>
      </StaticUiBridge>
    </AuthProvider>
  );
}
