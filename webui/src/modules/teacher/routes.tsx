import { Route, Routes, useNavigate } from "react-router-dom";

import { NotFoundPage } from "@/app/NotFoundPage";
import { TeacherWorkspace, type TeacherPage } from "@/modules/teacher/workspace/TeacherWorkspace";

export function TeacherRoutes() {
  const navigate = useNavigate();
  const page = (next: TeacherPage) => navigate(next === "overview" ? "/teacher" : `/teacher/${next}`);
  return (
    <Routes>
      <Route index element={<TeacherWorkspace page="overview" onNavigate={page} />} />
      <Route path="topics" element={<TeacherWorkspace page="topics" onNavigate={page} />} />
      <Route path="book" element={<TeacherWorkspace page="book" onNavigate={page} />} />
      <Route path="exercises" element={<TeacherWorkspace page="exercises" onNavigate={page} />} />
      <Route path="reviews" element={<TeacherWorkspace page="reviews" onNavigate={page} />} />
      <Route path="guided" element={<TeacherWorkspace page="guided" onNavigate={page} />} />
      <Route path="questions" element={<TeacherWorkspace page="questions" onNavigate={page} />} />
      <Route path="reports" element={<TeacherWorkspace page="reports" onNavigate={page} />} />
      <Route path="quota" element={<TeacherWorkspace page="quota" onNavigate={page} />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}

export default TeacherRoutes;
