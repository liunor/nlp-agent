import { lazy } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { AppShell } from "./layouts/AppShell";
import { NotFoundPage } from "./NotFoundPage";
import { AuthGate } from "@/modules/auth/AuthGate";
import { RouteGuard } from "./RouteGuard";
import { LoginPage } from "@/modules/auth/LoginPage";
import { ProfilePage } from "@/modules/profile/ProfilePage";

const StudentRoutes = lazy(() => import("@/modules/student").then(({ StudentRoutes: route }) => ({ default: route })));
const TeacherRoutes = lazy(() => import("@/modules/teacher").then(({ TeacherRoutes: route }) => ({ default: route })));
const DeveloperRoutes = lazy(() => import("@/modules/developer").then(({ DeveloperRoutes: route }) => ({ default: route })));
const AdminRoutes = lazy(() => import("@/modules/admin").then(({ AdminRoutes: route }) => ({ default: route })));

export function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Public route: standalone login page */}
        <Route path="/login" element={<LoginPage />} />

        {/* The student home is previewable as a guest; protected actions open the login dialog. */}
        <Route element={<AuthGate allowGuest><AppShell /></AuthGate>}>
          <Route index element={<StudentRoutes />} />
        </Route>

        {/* Protected routes: require authentication via AuthGate */}
        <Route element={<AuthGate><AppShell /></AuthGate>}>
          {/* Student routes: accessible to all authenticated users */}
          {/* Profile / self-service settings — all authenticated users */}
          <Route path="profile" element={<ProfilePage />} />

          {/* Teacher routes: require teacher or developer role */}
          <Route path="teacher/*" element={
            <RouteGuard allowedRoles={["teacher", "developer", "admin"]}>
              <TeacherRoutes />
            </RouteGuard>
          } />

          {/* Developer routes: require developer role */}
          <Route path="developer/*" element={
            <RouteGuard allowedRoles={["developer", "admin"]}>
              <DeveloperRoutes />
            </RouteGuard>
          } />

          {/* Admin routes: require developer role */}
          <Route path="admin/*" element={
            <RouteGuard allowedRoles={["developer", "admin"]}>
              <AdminRoutes />
            </RouteGuard>
          } />
          <Route path="*" element={<NotFoundPage />} />
        </Route>

        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </BrowserRouter>
  );
}
