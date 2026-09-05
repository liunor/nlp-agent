const STORAGE_NAMESPACE = "nlp-agent.imported-files.v1";

export interface ImportedFile {
  id: string;
  name: string;
  content: string;
  language: "markdown" | "text" | "code";
  codeLanguage: string;
  bytes: number;
  truncated: boolean;
  importedAt: number;
}

export function importedFilesStorageKey(userId: string | null, workspaceId: string) {
  const user = encodeURIComponent(userId?.trim() || "guest");
  const workspace = encodeURIComponent(workspaceId.trim() || "default");
  return STORAGE_NAMESPACE + ":" + user + ":" + workspace;
}

export function loadImportedFiles(key: string): ImportedFile[] {
  try {
    const raw = JSON.parse(localStorage.getItem(key) ?? "[]") as ImportedFile[];
    return Array.isArray(raw) ? raw.filter((item) => item && typeof item.name === "string" && typeof item.content === "string") : [];
  } catch {
    return [];
  }
}

/**
 * Removes the current user/workspace file cache. Privacy-sensitive local notes
 * should not survive a logout even when another account logs in on the same
 * browser. The per-key isolation still prevents cross-account reads, but
 * deleting the key on logout avoids leaving dormant copies behind.
 */
export function clearImportedFiles(userId: string | null, workspaceId: string) {
  try {
    localStorage.removeItem(importedFilesStorageKey(userId, workspaceId));
  } catch {
    // Storage can be unavailable in private browsing; removal is best-effort.
  }
}
