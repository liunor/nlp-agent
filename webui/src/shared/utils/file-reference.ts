export function fileSpaceViewUrl(fileId: string, workspaceId?: string, expectedHash?: string) {
  const query = new URLSearchParams();
  if (workspaceId) query.set("workspace_id", workspaceId);
  if (expectedHash) query.set("expected_hash", expectedHash);
  return `/api/v1/storage/files/${encodeURIComponent(fileId)}/view${query.size ? `?${query}` : ""}`;
}
