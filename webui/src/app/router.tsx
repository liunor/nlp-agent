import { lazy } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { AppShell } from "./layouts/AppShell";
import { NotFoundPage } from "./NotFoundPage";
import { AuthGate } from "@/modules/auth/AuthGate";
import { LoginPage } from "@/modules/auth/LoginPage";

const StudentRoutes = lazy(() => import("@/modules/student").then(({ StudentRoutes: route }) => ({ default: route })));
const TeacherRoutes = lazy(() => import("@/modules/teacher").then(({ TeacherRoutes: route }) => ({ default: route })));
const DeveloperRoutes = lazy(() => import("@/modules/developer").then(({ DeveloperRoutes: route }) => ({ default: route })));
const AdminRoutes = lazy(() => import("@/modules/admin").then(({ AdminRoutes: route }) => ({ default: route })));

export function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<AuthGate><AppShell /></AuthGate>}>
          <Route index element={<StudentRoutes />} />
          <Route path="teacher/*" element={<TeacherRoutes />} />
          <Route path="developer/*" element={<DeveloperRoutes />} />
          <Route path="admin/*" element={<AdminRoutes />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
