import { Route, Routes, useNavigate } from "react-router-dom";

import { NotFoundPage } from "@/app/NotFoundPage";
import { DeveloperWorkspace, type DeveloperPage } from "@/modules/developer/workspace/DeveloperWorkspace";

export function DeveloperRoutes() {
  const navigate = useNavigate();
  const page = (next: DeveloperPage) => navigate(next === "overview" ? "/developer" : `/developer/${next}`);
  return (
    <Routes>
      <Route index element={<DeveloperWorkspace page="overview" onNavigate={page} />} />
      <Route path="agents" element={<DeveloperWorkspace page="agents" onNavigate={page} />} />
      <Route path="tools" element={<DeveloperWorkspace page="tools" onNavigate={page} />} />
      <Route path="models" element={<DeveloperWorkspace page="models" onNavigate={page} />} />
      <Route path="mcp" element={<DeveloperWorkspace page="mcp" onNavigate={page} />} />
      <Route path="skills" element={<DeveloperWorkspace page="skills" onNavigate={page} />} />
      <Route path="release-notes" element={<DeveloperWorkspace page="release-notes" onNavigate={page} />} />
      <Route path="automations" element={<DeveloperWorkspace page="automations" onNavigate={page} />} />
      <Route path="feedback" element={<DeveloperWorkspace page="feedback" onNavigate={page} />} />
      <Route path="settings" element={<DeveloperWorkspace page="settings" onNavigate={page} />} />
      <Route path="users" element={<DeveloperWorkspace page="users" onNavigate={page} />} />
      <Route path="roles" element={<DeveloperWorkspace page="roles" onNavigate={page} />} />
      <Route path="audit" element={<DeveloperWorkspace page="audit" onNavigate={page} />} />
      <Route path="sessions" element={<DeveloperWorkspace page="sessions" onNavigate={page} />} />
      <Route path="quotas" element={<DeveloperWorkspace page="quotas" onNavigate={page} />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}

export default DeveloperRoutes;
