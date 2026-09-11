import { Suspense } from "react";
import { Outlet } from "react-router-dom";

import { LoginDialog } from "@/modules/student/components/LoginDialog";
import { useAuth } from "@/platform/auth/AuthContext";

export function AppShell() {
  const auth = useAuth();

  return (
    <>
      <Suspense
        fallback={
          <div className="boot-screen" role="status">
            <span className="boot-orbit" />
            <strong>正在进入 NLP 学习空间</strong>
            <p>连接教学 Agent 与学习记录……</p>
          </div>
        }
      >
        <Outlet />
      </Suspense>

      <LoginDialog
        open={auth.isAuthExpired}
        expired
        onClose={() => undefined}
        onAuthenticate={async (username, password) => {
          await auth.login(username, password);
        }}
      />
    </>
  );
}
