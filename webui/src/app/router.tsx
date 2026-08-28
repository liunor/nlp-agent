import { lazy, useState } from "react";
import { createBrowserRouter, RouterProvider } from "react-router-dom";

import { AppShell } from "./layouts/AppShell";
import { NotFoundPage } from "./NotFoundPage";
import { AuthGate } from "@/modules/auth/AuthGate";
import { LoginPage } from "@/modules/auth/LoginPage";

const StudentRoutes = lazy(() => import("@/modules/student").then(({ StudentRoutes: route }) => ({ default: route })));
const TeacherRoutes = lazy(() => import("@/modules/teacher").then(({ TeacherRoutes: route }) => ({ default: route })));
const DeveloperRoutes = lazy(() => import("@/modules/developer").then(({ DeveloperRoutes: route }) => ({ default: route })));
const AdminRoutes = lazy(() => import("@/modules/admin").then(({ AdminRoutes: route }) => ({ default: route })));

function createAppRouter() {
  return createBrowserRouter([
    { path: "/login", element: <LoginPage /> },
    {
      element: <AuthGate><AppShell /></AuthGate>,
      children: [
        { index: true, element: <StudentRoutes /> },
        { path: "teacher/*", element: <TeacherRoutes /> },
        { path: "developer/*", element: <DeveloperRoutes /> },
        { path: "admin/*", element: <AdminRoutes /> },
        { path: "*", element: <NotFoundPage /> },
      ],
    },
  ]);
}

// Exported for tests that need a fresh instance per render.
export const router = createAppRouter();

export function AppRouter() {
  // Create a fresh router per App mount so tests that pushState before render
  // see the expected initial location (createBrowserRouter reads window.location at creation).
  const [routerInstance] = useState(() => createAppRouter());
  return <RouterProvider router={routerInstance} />;
}
